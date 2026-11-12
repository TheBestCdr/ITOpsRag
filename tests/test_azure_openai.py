"""Tests for the Azure OpenAI generator using a fake client (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from itops_rag.azure_openai import AzureOpenAIGenerator
from itops_rag.config import Settings
from itops_rag.generation import NO_EVIDENCE
from itops_rag.retrieval import RetrievedChunk


def _chunk(chunk_id: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("_chunk")[0],
        title="T",
        doc_type="incident_report",
        text=text,
        score=1.0,
        rank=0,
    )


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        )


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(content))


def _settings() -> Settings:
    return Settings(min_similarity=0.35, max_output_tokens=200)


def test_generator_maps_markers_to_chunk_ids():
    client = FakeClient("The target is 10 minutes [1].")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    chunks = [_chunk("doc_0001_chunk_1", "acknowledge within 10 minutes")]
    answer = generator.generate("q", chunks, best_similarity=0.9)

    assert answer.grounded is True
    assert answer.citations == ["doc_0001_chunk_1"]
    assert answer.usage["total_tokens"] == 120


def test_generator_maps_multiple_markers():
    client = FakeClient("First fact [1]. Second fact [2].")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    chunks = [
        _chunk("doc_0001_chunk_1", "a"),
        _chunk("doc_0002_chunk_1", "b"),
    ]
    answer = generator.generate("q", chunks, best_similarity=0.9)

    assert answer.citations == ["doc_0001_chunk_1", "doc_0002_chunk_1"]


def test_generator_refuses_when_model_says_insufficient():
    client = FakeClient(NO_EVIDENCE)
    generator = AzureOpenAIGenerator(_settings(), client=client)

    answer = generator.generate("q", [_chunk("doc_0001_chunk_1", "a")], best_similarity=0.9)
    assert answer.grounded is False
    assert answer.citations == []


def test_generator_refuses_without_citations():
    """An answer with no [n] marker is not grounded, so it must be refused."""
    client = FakeClient("Some confident but uncited claim.")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    answer = generator.generate("q", [_chunk("doc_0001_chunk_1", "a")], best_similarity=0.9)
    assert answer.grounded is False


def test_generator_refuses_below_similarity_threshold():
    client = FakeClient("Should not be called [1].")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    answer = generator.generate("q", [_chunk("doc_0001_chunk_1", "a")], best_similarity=0.1)
    assert answer.grounded is False
    assert client.chat.completions.calls == []


def test_generator_ignores_out_of_range_markers():
    client = FakeClient("Claim [9].")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    answer = generator.generate("q", [_chunk("doc_0001_chunk_1", "a")], best_similarity=0.9)
    assert answer.grounded is False


def test_generator_sends_evidence_and_question():
    client = FakeClient("Answer [1].")
    generator = AzureOpenAIGenerator(_settings(), client=client)

    generator.generate("what is the target?", [_chunk("doc_0001_chunk_1", "10 minutes")], 0.9)

    call = client.chat.completions.calls[0]
    user_message = call["messages"][1]["content"]
    assert "10 minutes" in user_message
    assert "what is the target?" in user_message
    assert call["temperature"] == 0
    assert call["max_tokens"] == 200


def test_generator_uses_reasoning_params_for_gpt5():
    """Reasoning models reject temperature/max_tokens and need max_completion_tokens."""
    client = FakeClient("Answer [1].")
    settings = Settings(
        min_similarity=0.35,
        max_output_tokens=200,
        azure_chat_deployment="gpt-5-mini",
    )
    generator = AzureOpenAIGenerator(settings, client=client)

    generator.generate("q", [_chunk("doc_0001_chunk_1", "10 minutes")], 0.9)

    call = client.chat.completions.calls[0]
    assert "temperature" not in call
    assert "max_tokens" not in call
    assert call["max_completion_tokens"] >= 200
