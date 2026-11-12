"""Text helpers shared by retrieval, answering, and evaluation.

These are lifted from the notebook so the deployed system behaves identically.
"""

from __future__ import annotations

import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-z0-9]+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
QUOTED_RE = re.compile(r"'([^']+)'")
SEVERITY_RE = re.compile(r"severity\s+(\d)", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Lowercase and keep only letters/digits."""
    return TOKEN_RE.findall(text.lower())


def split_sentences(text: str) -> list[str]:
    """Split a chunk into sentences."""
    return [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]


def quoted_titles(question: str) -> list[str]:
    """Titles the question quotes verbatim, e.g. 'EU cdn_issue incident report'."""
    return [q.strip() for q in QUOTED_RE.findall(question)]


def token_f1(prediction: str, reference: str) -> float:
    """Word-overlap F1 between two strings."""
    pred_tokens = tokenize(prediction)
    ref_tokens = tokenize(reference)
    if not pred_tokens or not ref_tokens:
        return 0.0

    overlap = Counter(pred_tokens) & Counter(ref_tokens)
    shared = sum(overlap.values())

    precision = shared / len(pred_tokens)
    recall = shared / len(ref_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
