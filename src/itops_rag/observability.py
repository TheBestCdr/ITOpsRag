"""Structured logging, request correlation, and optional tracing.

Tracing is opt-in: if LangSmith or Application Insights credentials are absent
the app runs normally with structured logs only.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from .config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, so Azure can query the fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in ("method", "path", "status", "latency_ms", "question_len", "citations"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter on the root logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Azure SDK logs are noisy at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def configure_tracing(settings: Settings) -> None:
    """Enable LangSmith and/or OpenTelemetry when credentials are present."""
    if settings.langsmith_tracing and settings.langsmith_api_key:
        import os

        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
        logging.getLogger(__name__).info("LangSmith tracing enabled")

    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(
                connection_string=settings.applicationinsights_connection_string,
                logger_name="itops_rag",
            )
            logging.getLogger(__name__).info("Application Insights tracing enabled")
        except Exception:  # pragma: no cover - optional dependency
            logging.getLogger(__name__).warning(
                "Application Insights tracing unavailable", exc_info=True
            )


class Timer:
    """Context manager that records elapsed milliseconds."""

    def __init__(self) -> None:
        self.elapsed_ms = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
