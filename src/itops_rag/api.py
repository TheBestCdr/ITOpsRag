"""FastAPI application: /ask, /search, /healthz, /readyz, /feedback.

Hardening included:
* optional API-key auth (X-API-Key)
* per-IP rate limiting
* daily request cap
* kill switch
* request correlation IDs
* structured JSON logs
* timeouts and retries on outbound calls (configured in the clients)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .corpus import load_corpus
from .observability import (
    configure_logging,
    configure_tracing,
    new_request_id,
    request_id_var,
)
from .service import RagService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- schemas


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    method: str = Field(default="hybrid", pattern="^(bm25|vector|hybrid)$")
    k: int = Field(default=5, ge=1, le=20)


class SearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    method: str = Field(default="hybrid", pattern="^(bm25|vector|hybrid)$")
    k: int = Field(default=5, ge=1, le=20)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    answer: str = Field(max_length=4000)
    helpful: bool
    comment: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- limits


class RateLimiter:
    """Simple in-memory sliding-window limiter.

    Good enough for a single-replica dev deployment. A multi-replica production
    deployment would move this to Redis or API Management.
    """

    def __init__(self, per_minute: int, daily_cap: int) -> None:
        self.per_minute = per_minute
        self.daily_cap = daily_cap
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._day_start = time.time()
        self._day_count = 0

    def check(self, client_id: str) -> None:
        now = time.time()

        # Reset the daily counter every 24h
        if now - self._day_start > 86400:
            self._day_start = now
            self._day_count = 0

        if self._day_count >= self.daily_cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily request cap reached. Try again tomorrow.",
            )

        window = self._hits[client_id]
        while window and now - window[0] > 60:
            window.popleft()

        if len(window) >= self.per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Slow down.",
            )

        window.append(now)
        self._day_count += 1


# ---------------------------------------------------------------- app


def create_app(settings: Settings | None = None, service: RagService | None = None) -> FastAPI:
    """Application factory. Tests inject a prebuilt service."""
    settings = settings or get_settings()

    configure_logging(settings)
    configure_tracing(settings)

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Grounded RAG over the Kaggle AgentEval IT operations corpus.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "local" else [],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    limiter = RateLimiter(settings.rate_limit_per_minute, settings.daily_request_cap)

    # Build the service once at startup. Tests pass their own.
    state: dict[str, RagService | None] = {"service": service}

    def get_service() -> RagService:
        if state["service"] is None:
            # The local retriever needs the corpus in memory. The Azure backend
            # reads chunks from Azure AI Search, so the corpus is optional there
            # and the container image does not ship the Kaggle data.
            corpus = None
            if settings.retriever_backend.lower() == "local":
                corpus = load_corpus(settings)
            state["service"] = RagService(settings, corpus)
        return state["service"]  # type: ignore[return-value]

    # ---- middleware: correlation id + access log ----

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": round(latency_ms, 1),
                },
            )
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response

    # ---- auth dependency ----

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_key and x_api_key != settings.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-API-Key",
            )

    def guard(request: Request) -> None:
        if settings.kill_switch:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily disabled by operator.",
            )
        client_id = request.client.host if request.client else "unknown"
        limiter.check(client_id)

    # ---- endpoints ----

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(svc: RagService = Depends(get_service)) -> dict:
        """Readiness: the knowledge base is loaded and the backends are configured."""
        info = svc.health()
        info["status"] = "ready"
        return info

    @app.post("/ask", dependencies=[Depends(require_api_key), Depends(guard)])
    def ask(payload: AskRequest, svc: RagService = Depends(get_service)) -> dict:
        """Answer a question with grounded citations."""
        result = svc.ask(payload.question, method=payload.method, k=payload.k)
        logger.info(
            "ask",
            extra={
                "question_len": len(payload.question),
                "citations": len(result.citations),
                "latency_ms": round(result.latency_ms, 1),
            },
        )
        return {
            "question": result.question,
            "answer": result.answer,
            "citations": result.citations,
            "grounded": result.grounded,
            "method": result.method,
            "evidence": result.evidence,
            "latency_ms": round(result.latency_ms, 1),
            "usage": result.usage,
        }

    @app.post("/search", dependencies=[Depends(require_api_key), Depends(guard)])
    def search(payload: SearchRequest, svc: RagService = Depends(get_service)) -> dict:
        """Return retrieval results without generating an answer."""
        hits = svc.search(payload.question, method=payload.method, k=payload.k)
        return {"question": payload.question, "method": payload.method, "results": hits}

    @app.post("/feedback", dependencies=[Depends(require_api_key)])
    def feedback(payload: FeedbackRequest) -> dict:
        """Record user feedback. Logged as structured data for later analysis."""
        logger.info(
            "feedback",
            extra={
                "question_len": len(payload.question),
                "citations": 1 if payload.helpful else 0,
            },
        )
        return {"status": "recorded", "helpful": payload.helpful}

    # ---- static frontend ----

    web_dir = Path(settings.web_dist_dir)
    if web_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        @app.get("/{path:path}")
        def spa_fallback(path: str):
            """Serve the SPA for any non-API route."""
            candidate = web_dir / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_dir / "index.html")

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id_var.get()},
        )

    return app


app = create_app()
