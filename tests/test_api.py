"""API tests: endpoints, auth, rate limiting, kill switch, correlation IDs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from itops_rag.api import create_app

from .conftest import requires_data


@pytest.fixture()
def client(settings, service):
    app = create_app(settings, service)
    return TestClient(app)


@requires_data
def test_healthz_is_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@requires_data
def test_readyz_reports_full_corpus(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["documents"] == 1200
    assert body["chunks"] == 3600


@requires_data
def test_ask_returns_answer_and_citations(client):
    response = client.post(
        "/ask",
        json={"question": "What is the acknowledge and mitigation target for Severity 4 incidents?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert body["citations"]
    assert body["evidence"]


@requires_data
def test_ask_refuses_out_of_scope(client):
    response = client.post("/ask", json={"question": "How do I bake sourdough bread?"})
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["citations"] == []


@requires_data
def test_search_returns_results_without_answer(client):
    response = client.post("/search", json={"question": "cdn outage root cause", "k": 3})
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 3
    assert "answer" not in body


@requires_data
def test_request_id_header_is_returned(client):
    response = client.get("/healthz")
    assert response.headers.get("X-Request-ID")


@requires_data
def test_request_id_is_echoed_when_supplied(client):
    response = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


@requires_data
def test_invalid_method_is_rejected(client):
    response = client.post("/ask", json={"question": "cdn outage", "method": "magic"})
    assert response.status_code == 422


@requires_data
def test_short_question_is_rejected(client):
    response = client.post("/ask", json={"question": "hi"})
    assert response.status_code == 422


@requires_data
def test_feedback_is_recorded(client):
    response = client.post(
        "/feedback",
        json={"question": "cdn outage root cause", "answer": "x", "helpful": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "recorded"


@requires_data
def test_api_key_is_enforced_when_configured(settings, service):
    secured = settings.model_copy(update={"api_key": "secret-key"})
    client = TestClient(create_app(secured, service))

    assert client.post("/ask", json={"question": "cdn outage root cause"}).status_code == 401

    ok = client.post(
        "/ask",
        json={"question": "cdn outage root cause"},
        headers={"X-API-Key": "secret-key"},
    )
    assert ok.status_code == 200


@requires_data
def test_kill_switch_blocks_ask(settings, service):
    stopped = settings.model_copy(update={"kill_switch": True})
    client = TestClient(create_app(stopped, service))

    response = client.post("/ask", json={"question": "cdn outage root cause"})
    assert response.status_code == 503
    # Health checks must keep working so the platform can still probe the app.
    assert client.get("/healthz").status_code == 200


@requires_data
def test_rate_limit_returns_429(settings, service):
    limited = settings.model_copy(update={"rate_limit_per_minute": 2})
    client = TestClient(create_app(limited, service))

    payload = {"question": "cdn outage root cause"}
    assert client.post("/ask", json=payload).status_code == 200
    assert client.post("/ask", json=payload).status_code == 200
    assert client.post("/ask", json=payload).status_code == 429
