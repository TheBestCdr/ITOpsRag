"""Azure OpenAI clients: embeddings and grounded chat generation.

Authentication prefers Microsoft Entra ID (managed identity in Azure) and falls
back to an API key for local development. No credential is ever hardcoded.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np

from .config import Settings
from .generation import NO_EVIDENCE, Answer
from .retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an IT operations assistant. Answer ONLY from the numbered evidence "
    "passages provided. If the passages do not contain the answer, reply exactly: "
    f"{NO_EVIDENCE} "
    "Cite the passage numbers you used in square brackets, for example [1] or [2][3]. "
    "Be concise: at most three sentences. Never use outside knowledge."
)


def _build_openai_client(settings: Settings):
    """Create an Azure OpenAI client using Entra ID when available."""
    from openai import AzureOpenAI

    if not settings.azure_openai_endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")

    if settings.azure_openai_api_key:
        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )

    # Managed identity / az login path
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=settings.azure_openai_api_version,
        timeout=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
    )


class AzureOpenAIEmbedder:
    """Embedding backend backed by Azure OpenAI.

    ``dimensions`` is passed through so the index can use 512-dim vectors and
    stay comfortably inside the Azure AI Search Free tier storage cap.
    """

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_openai_client(self.settings)
        return self._client

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts, returning L2-normalized float32 vectors."""
        vectors: list[list[float]] = []
        # The API caps a single request at 2048 inputs and 300K tokens, but the
        # binding limit in practice is the deployment's tokens-per-minute quota.
        # Small batches plus retry/backoff keep indexing inside a low TPM tier.
        batch_size = max(1, self.settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = self._embed_batch_with_retry(batch)
            vectors.extend(item.embedding for item in response.data)

        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def _embed_batch_with_retry(self, batch: list[str]):
        """Call the embeddings API, backing off on 429 rate limits."""
        import time

        from openai import RateLimitError

        attempts = self.settings.embedding_max_retries
        delay = 5.0
        for attempt in range(1, attempts + 1):
            try:
                return self.client.embeddings.create(
                    model=self.settings.azure_embedding_deployment,
                    input=batch,
                    dimensions=self.settings.azure_embedding_dimensions,
                )
            except RateLimitError:
                if attempt == attempts:
                    raise
                logger.warning(
                    "embedding rate limited (attempt %d/%d); sleeping %.0fs",
                    attempt,
                    attempts,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable")

    def embed_question(self, question: str) -> np.ndarray:
        return self.embed_texts([question])[0]


class AzureOpenAIGenerator:
    """Grounded answer generation with Azure OpenAI."""

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_openai_client(self.settings)
        return self._client

    def _format_evidence(self, retrieved: Sequence[RetrievedChunk]) -> str:
        lines = []
        for i, chunk in enumerate(retrieved, start=1):
            lines.append(f"[{i}] ({chunk.chunk_id}) {chunk.title}\n{chunk.text}")
        return "\n\n".join(lines)

    def _completion_params(self) -> dict:
        """Chat parameters adapted to the deployed model.

        Reasoning models (``gpt-5*`` and the ``o1``/``o3``/``o4`` families)
        reject ``temperature`` and ``max_tokens``; they require
        ``max_completion_tokens`` with a budget large enough to cover the hidden
        reasoning tokens before the visible answer. Non-reasoning models keep
        the original deterministic settings.
        """
        deployment = (self.settings.azure_chat_deployment or "").lower()
        if deployment.startswith(("gpt-5", "o1", "o3", "o4")):
            # Reasoning tokens count against this budget, so keep a floor well
            # above max_output_tokens to avoid truncating the answer.
            return {"max_completion_tokens": max(self.settings.max_output_tokens, 4000)}
        return {"temperature": 0, "max_tokens": self.settings.max_output_tokens}

    def generate(
        self,
        question: str,
        retrieved: Sequence[RetrievedChunk],
        best_similarity: float,
        method: str = "hybrid",
    ) -> Answer:
        # Same abstention rule as the extractive generator, so behaviour is
        # comparable between backends.
        if best_similarity < self.settings.min_similarity or not retrieved:
            return Answer(answer=NO_EVIDENCE, citations=[], grounded=False, method=method)

        evidence = self._format_evidence(retrieved)
        user_prompt = f"Evidence passages:\n\n{evidence}\n\nQuestion: {question}"

        response = self.client.chat.completions.create(
            model=self.settings.azure_chat_deployment,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            **self._completion_params(),
        )

        text = (response.choices[0].message.content or "").strip()
        usage = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        if not text or NO_EVIDENCE.lower() in text.lower():
            return Answer(answer=NO_EVIDENCE, citations=[], grounded=False, method=method, usage=usage)

        # Map the [n] markers the model emitted back to real chunk IDs.
        cited: list[str] = []
        for marker in sorted(set(int(m) for m in __import__("re").findall(r"\[(\d+)\]", text))):
            if 1 <= marker <= len(retrieved):
                chunk_id = retrieved[marker - 1].chunk_id
                if chunk_id not in cited:
                    cited.append(chunk_id)

        # A grounded answer must cite something; otherwise treat it as a refusal.
        if not cited:
            return Answer(answer=NO_EVIDENCE, citations=[], grounded=False, method=method, usage=usage)

        return Answer(answer=text, citations=cited, grounded=True, method=method, usage=usage)
