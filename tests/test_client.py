import json
from urllib.error import HTTPError

import pytest
from jev_plugin_for_hermes.client import (
    JevClient,
    JevConfigurationError,
    JevHTTPError,
    JevProtocolError,
    JevTransportError,
    JevValidationError,
    build_request,
)


class FakeResponse:
    def __init__(self, payload, status=200, raw: bytes | None = None):
        self.status = status
        self._payload = payload
        self._raw = raw

    def read(self):
        if self._raw is not None:
            return self._raw
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


@pytest.mark.parametrize(
    ("state", "questions", "match"),
    [
        ("", {"q": {"type": "noul", "instructions": "x?"}}, "state must not be empty"),
        ({"bad": object()}, {"q": {"type": "noul", "instructions": "x?"}}, "JSON-compatible"),
        ("x" * 10, {"q": {"type": "noul", "instructions": "x?"}}, "exceeds the configured limit"),
        ("ok", {"1bad": {"type": "noul", "instructions": "x?"}}, "invalid question name"),
        ("ok", {"q": {"type": "noul", "instructions": "   "}}, "needs non-empty instructions"),
        ("ok", {"q": {"type": "choice", "instructions": "Pick", "criteria": {}}}, "non-empty criteria object"),
        (
            "ok",
            {"q": {"type": "score", "instructions": "Rate", "criteria": ["only one"]}},
            "at least two non-empty criteria",
        ),
        ("ok", {}, "non-empty object"),
    ],
)
def test_build_request_validation_fail_closed(state, questions, match):
    max_chars = 5 if "exceeds" in match else 20000
    with pytest.raises(JevValidationError, match=match):
        build_request(state, questions, "jev-latest", max_chars)


def test_build_request_accepts_score_and_choice():
    request = build_request(
        "evidence",
        {
            "risk": {
                "type": "score",
                "instructions": "How risky?",
                "criteria": ["low", "high"],
            },
            "kind": {
                "type": "choice",
                "instructions": "Which?",
                "criteria": {"a": "Option A"},
            },
        },
        "jev-latest",
        20000,
    )
    assert request.questions["risk"]["criteria"] == ["low", "high"]
    assert request.questions["kind"]["criteria"] == {"a": "Option A"}


@pytest.mark.parametrize(
    ("endpoint", "error_match"),
    [
        ("http://example.com/v1", "HTTPS except for local"),
        ("https://user:pass@api.example.com/v1", "credentials"),
        ("https://api.example.com/v1?x=1", "query parameters"),
        ("https://api.example.com/v1#frag", "fragments"),
    ],
)
def test_endpoint_validation_rejects_unsafe_urls(endpoint, error_match):
    with pytest.raises(JevConfigurationError, match=error_match):
        JevClient(api_key="x", endpoint=endpoint)


def test_endpoint_allows_localhost_http():
    client = JevClient(api_key="x", endpoint="http://127.0.0.1:8080/v1/systemone")
    assert client.endpoint == "http://127.0.0.1:8080/v1/systemone"


def test_client_clamps_timeout_and_max_state_chars():
    client = JevClient(api_key="x", timeout=0.5, max_state_chars=10_000_000)
    assert client.timeout == 1.0
    assert client.max_state_chars == 200_000


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


def test_http_error_from_opener_maps_to_jev_http_error():
    def opener(*args, **kwargs):
        raise HTTPError("https://api.typesafe.ai/v1/systemone", 503, "Service Unavailable", {}, None)

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevHTTPError) as exc_info:
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )
    assert exc_info.value.status == 503


def test_transport_error_is_explicit():
    def opener(*args, **kwargs):
        raise OSError("network down")

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevTransportError, match="could not reach"):
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )


@pytest.mark.parametrize(
    ("payload", "raw", "match"),
    [
        ({}, None, "answers object"),
        ({"answers": "not-a-dict"}, None, "answers object"),
        ({}, b"not-json", "invalid JSON"),
    ],
)
def test_protocol_errors_fail_closed(payload, raw, match):
    def opener(*args, **kwargs):
        return FakeResponse(payload, raw=raw)

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevProtocolError, match=match):
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )
