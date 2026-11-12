# Multi-stage build: React -> static assets, then a slim Python runtime.
# The deployed image uses Azure OpenAI for embeddings, so torch and
# sentence-transformers are NOT installed (keeps the image small and cold
# starts fast). The local fallback is available via the "local" extra.

# ---------- stage 1: build the React frontend ----------
FROM node:22-alpine AS web

WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- stage 2: python runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_DIST_DIR=/app/web/dist \
    ENVIRONMENT=azure \
    RETRIEVER_BACKEND=azure \
    EMBEDDING_BACKEND=azure \
    GENERATOR_BACKEND=azure_openai

WORKDIR /app

# curl is used by the container health probe.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install the package with the Azure extras only (no torch).
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[azure,observability]"

# Copy the built frontend from stage 1.
COPY --from=web /web/dist ./web/dist

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Container Apps probes /healthz; readiness uses /readyz.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "itops_rag.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
