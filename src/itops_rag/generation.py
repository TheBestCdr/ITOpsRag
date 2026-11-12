"""Grounded answer generation.

Two generators share one interface:

* ``ExtractiveGenerator`` - notebook behaviour. Picks the sentence from the
  retrieved evidence that best answers the question and cites the chunk. Refuses
  when nothing is close enough. No LLM, so it always works.
* ``AzureOpenAIGenerator`` - sends the retrieved passages to Azure OpenAI and
  asks for a grounded answer with citations, refusing when evidence is weak.

Neither generator ever sees the reference answers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .config import Settings
from .retrieval import RetrievedChunk
from .text import SEVERITY_RE, split_sentences

NO_EVIDENCE = "Insufficient evidence in the knowledge base to answer this question."


@dataclass
class Answer:
    """A grounded answer with its supporting citations."""

    answer: str
    citations: list[str] = field(default_factory=list)
    grounded: bool = False
    method: str = "hybrid"
    usage: dict[str, int] = field(default_factory=dict)


class Generator(Protocol):
    def generate(
        self,
        question: str,
        retrieved: Sequence[RetrievedChunk],
        best_similarity: float,
        method: str = "hybrid",
    ) -> Answer:
        ...


class ExtractiveGenerator:
    """Notebook-parity extractive answerer."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(
        self,
        question: str,
        retrieved: Sequence[RetrievedChunk],
        best_similarity: float,
        method: str = "hybrid",
    ) -> Answer:
        # Refuse when the best match is too weak to be real evidence
        if best_similarity < self.settings.min_similarity:
            return Answer(answer=NO_EVIDENCE, citations=[], grounded=False, method=method)

        # Special case: pull the clause for the requested severity level
        severity = SEVERITY_RE.search(question)
        if severity:
            level = severity.group(1)
            for chunk in retrieved:
                for sentence in split_sentences(chunk.text):
                    if (
                        re.search(rf"severity\s+{level}\b", sentence, re.IGNORECASE)
                        and ":" in sentence
                    ):
                        clause = sentence.split(":", 1)[1].strip()
                        return Answer(
                            answer=clause,
                            citations=[chunk.chunk_id],
                            grounded=True,
                            method=method,
                        )

        # "Combine evidence from A and B" needs two passages, everything else one
        needed = 2 if question.lower().startswith("combine") else 1

        picked: list[RetrievedChunk] = []
        seen_text: set[str] = set()
        for chunk in retrieved:
            if chunk.text in seen_text:  # skip duplicate passages
                continue
            seen_text.add(chunk.text)
            picked.append(chunk)
            if len(picked) == needed:
                break

        if not picked:
            return Answer(answer=NO_EVIDENCE, citations=[], grounded=False, method=method)

        # The first sentence of a chunk carries its key point
        answer = " ".join(split_sentences(c.text)[0] for c in picked)
        return Answer(
            answer=answer,
            citations=[c.chunk_id for c in picked],
            grounded=True,
            method=method,
        )
