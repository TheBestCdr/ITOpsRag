"""Configuration for the IT Operations RAG assistant.

All settings come from environment variables so the same image runs locally,
in CI, and in Azure Container Apps. Nothing here contains a secret.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "IT Operations AI Knowledge Assistant"
    environment: str = Field(default="local", description="local | ci | azure")
    log_level: str = "INFO"

    # ---- Corpus ----
    # Directory holding corpus.csv / train.csv / test.csv.
    # In Azure this is a mounted path or a downloaded blob; locally it is the
    # Kaggle folder. Never committed to Git.
    data_dir: Path = Field(default=Path("agent-eval-part-i-grounded-rag-benchmark"))
    expected_docs: int = 1200
    expected_chunks: int = 3600

    # ---- Retrieval ----
    # "local"  -> in-process BM25 + embeddings (notebook parity, no Azure needed)
    # "azure"  -> Azure AI Search
    retriever_backend: str = "local"
    top_k: int = 5
    rrf_k: int = 60
    candidate_pool: int = 50
    use_title_anchor: bool = True
    min_similarity: float = 0.35

    # ---- Embeddings ----
    # "local"  -> sentence-transformers all-MiniLM-L6-v2 (384 dims)
    # "azure"  -> Azure OpenAI text-embedding-3-small
    embedding_backend: str = "local"
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    azure_embedding_deployment: str = "text-embedding-3-small"
    azure_embedding_dimensions: int = 512
    # Texts per embeddings request. Kept small so a low tokens-per-minute quota
    # (e.g. 10K TPM) is not exceeded; raise it for higher-throughput tiers.
    embedding_batch_size: int = 32
    embedding_max_retries: int = 8

    # ---- Answer generation ----
    # "extractive" -> notebook behaviour (no LLM, always available)
    # "azure_openai" -> grounded LLM answer with citations
    generator_backend: str = "extractive"
    # gpt-4o-mini is deprecated; gpt-5-mini is the current small chat model.
    azure_chat_deployment: str = "gpt-5-mini"
    max_output_tokens: int = 400
    request_timeout_seconds: float = 30.0
    max_retries: int = 2

    # ---- Azure OpenAI ----
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    # Prefer managed identity in Azure; the key is only for local development.
    azure_openai_api_key: str | None = None

    # ---- Azure AI Search ----
    azure_search_endpoint: str | None = None
    azure_search_index: str = "itops-chunks"
    azure_search_api_key: str | None = None

    # ---- Observability ----
    langsmith_api_key: str | None = None
    langsmith_project: str = "itops-rag"
    langsmith_tracing: bool = False
    applicationinsights_connection_string: str | None = None
    otel_service_name: str = "itops-rag"

    # ---- API protection ----
    # When set, callers must send X-API-Key. Empty means "no auth" (local dev).
    api_key: str | None = None
    rate_limit_per_minute: int = 30
    daily_request_cap: int = 2000
    # Set to true to make /ask and /search return 503 (emergency stop).
    kill_switch: bool = False

    # ---- Frontend ----
    # Directory containing the built React app. Empty disables static serving.
    web_dist_dir: Path = Field(default=Path("web/dist"))

    @property
    def corpus_path(self) -> Path:
        return self.data_dir / "corpus.csv"

    @property
    def train_path(self) -> Path:
        return self.data_dir / "train.csv"

    @property
    def test_path(self) -> Path:
        return self.data_dir / "test.csv"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance (one per process)."""
    return Settings()
