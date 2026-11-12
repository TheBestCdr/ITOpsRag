"""IT Operations AI Knowledge Assistant - grounded RAG over the AgentEval corpus."""

from .config import Settings, get_settings
from .corpus import Corpus, build_eval_set, load_corpus
from .generation import NO_EVIDENCE, Answer, ExtractiveGenerator
from .retrieval import LocalRetriever, RetrievedChunk
from .service import AskResult, RagService

__version__ = "1.0.0"

__all__ = [
    "Settings",
    "get_settings",
    "Corpus",
    "load_corpus",
    "build_eval_set",
    "Answer",
    "ExtractiveGenerator",
    "NO_EVIDENCE",
    "LocalRetriever",
    "RetrievedChunk",
    "AskResult",
    "RagService",
    "__version__",
]
