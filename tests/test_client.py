import json

import pytest
from jev_plugin_for_hermes.client import (
    JevClient,
    JevConfigurationError,
    JevHTTPError,
    JevValidationError,
    build_request,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_build_request_normalizes_state_and_questions():
    request = build_request(
        {"b": 2, "a": "x"},
        {
            "urgent": {"type": "NOUL", "instructions": " Is this urgent? "},
            "kind": {"type": "choice", "instructions": "Which kind?", "criteria": {"a": "A"}},
        },
        "jev-latest",
        20000,
    )
    assert request.state == '{"a":"x","b":2}'
    assert request.questions["urgent"]["type"] == "noul"
    assert request.questions["urgent"]["instructions"] == "Is this urgent?"


def test_client_sends_documented_payload_without_logging_secret():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(
            {
                "model": "jev-latest",
                "answers": {"urgent": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }
        )

    client = JevClient(api_key="secret-value", opener=opener)
    result = client.evaluate(
        state="hello",
        questions={"urgent": {"type": "noul", "instructions": "Is this urgent?"}},
    )

    assert result["answers"]["urgent"]["noul"] == 0.9
    request, timeout = calls[0]
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert request.get_header("Authorization") == "Bearer secret-value"
    assert timeout == 30.0
    payload = json.loads(request.data)
    assert payload["state"] == "hello"
    assert payload["model"] == "jev-latest"
    assert payload["questions"]["urgent"]["type"] == "noul"


def test_missing_key_fails_before_transport(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []

    def opener(*args, **kwargs):
        calls.append(True)
        raise AssertionError("transport must not run")

    client = JevClient(opener=opener)
    with pytest.raises(JevConfigurationError, match="missing TYPESAFE_API_KEY"):
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )
    assert calls == []


def test_invalid_questions_fail_closed():
    with pytest.raises(JevValidationError, match="score question"):
        build_request(
            "state",
            {"q": {"type": "score", "instructions": "How?", "criteria": ["only one"]}},
            "jev-latest",
            20000,
        )


def test_non_success_status_is_explicit():
    def opener(*args, **kwargs):
        return FakeResponse({}, status=429)

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevHTTPError) as exc_info:
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )
    assert exc_info.value.status == 429
