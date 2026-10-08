import asyncio
import json

import httpx
import pytest
from sentinel.agent import model_diagnose
from sentinel.scenarios import fixture


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_request_and_structured_response(monkeypatch, provider):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    diagnosis = {
        "cause": "pool_exhaustion",
        "summary": "Supported diagnosis",
        "confidence": 0.92,
        "evidence_ids": ["metrics-1", "deployment-1"],
        "recommendation": "rollback",
    }
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        if provider == "openai":
            assert request.headers["authorization"] == "Bearer test-key"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(diagnosis)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 25},
                },
            )
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(diagnosis)}],
                "usage": {"input_tokens": 100, "output_tokens": 25},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle))
    )
    result, usage = asyncio.run(model_diagnose(provider, "configured-test-model", fixture("pool_exhaustion")))
    assert result.cause == "pool_exhaustion"
    assert captured[0]["model"] == "configured-test-model"
    assert usage


def test_provider_failure_does_not_invent_diagnosis(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, json={"error": "rate limited"}))
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(model_diagnose("openai", "configured-model", fixture("pool_exhaustion")))
