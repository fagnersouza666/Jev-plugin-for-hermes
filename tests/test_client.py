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
    _MAX_RESPONSE_BYTES,
    _NoRedirect,
    _default_opener,
    build_request,
)


class FakeResponse:
    def __init__(self, payload, status=200, raw: bytes | None = None):
        self.status = status
        self._payload = payload
        self._raw = raw

    def read(self, n=-1):
        data = self._raw if self._raw is not None else json.dumps(self._payload).encode("utf-8")
        if n < 0:
            return data
        return data[:n]

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


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_no_redirect_handler_rejects_redirects(code):
    handler = _NoRedirect()
    assert handler.redirect_request(None, None, code, "", {}, "https://attacker.example/") is None


def test_default_opener_does_not_follow_redirects():
    opener = _default_opener()
    assert callable(opener)
    client = JevClient(api_key="x")
    handlers = client._opener.__self__.handlers
    assert any(isinstance(handler, _NoRedirect) for handler in handlers)


def test_oversized_response_body_fails_closed():
    def opener(*args, **kwargs):
        return FakeResponse({}, raw=b"x" * (_MAX_RESPONSE_BYTES + 1))

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevProtocolError, match="size limit"):
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )


def test_http_error_drains_and_closes_response():
    class DrainableBody:
        def __init__(self):
            self.read_calls = []
            self.closed = False

        def read(self, n=-1):
            self.read_calls.append(n)
            return b""

        def close(self):
            self.closed = True

    body = DrainableBody()
    error = HTTPError(
        "https://api.typesafe.ai/v1/systemone",
        503,
        "Service Unavailable",
        {},
        body,
    )

    def opener(*args, **kwargs):
        raise error

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevHTTPError) as exc_info:
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )
    assert exc_info.value.status == 503
    assert body.read_calls
    assert body.closed is True


@pytest.mark.parametrize(
    ("answers", "match"),
    [
        ({"answers": {}}, "answers object"),
        ({"answers": {"q": {"type": "noul", "noul": 0.5}, "extra": {"type": "noul"}}}, "answers object"),
        ({"answers": {"q": None}}, "answers object"),
        ({"answers": {"q": "not-a-dict"}}, "answers object"),
        ({"answers": {"q": {"type": "choice", "noul": 0.5}}}, "answers object"),
    ],
)
def test_parse_answers_rejects_incomplete_or_mismatched_shapes(answers, match):
    def opener(*args, **kwargs):
        return FakeResponse(answers)

    client = JevClient(api_key="x", opener=opener)
    with pytest.raises(JevProtocolError, match=match):
        client.evaluate(
            state="hello",
            questions={"q": {"type": "noul", "instructions": "Is this true?"}},
        )


def test_build_request_rejects_nan_and_infinity():
    with pytest.raises(JevValidationError, match="JSON-compatible"):
        build_request({"price": float("nan")}, {"q": {"type": "noul", "instructions": "x?"}}, "jev-latest", 20000)
    with pytest.raises(JevValidationError, match="JSON-compatible"):
        build_request({"price": float("inf")}, {"q": {"type": "noul", "instructions": "x?"}}, "jev-latest", 20000)


def test_build_request_rejects_too_many_questions():
    questions = {f"q{i}": {"type": "noul", "instructions": "x?"} for i in range(33)}
    with pytest.raises(JevValidationError, match="at most 32"):
        build_request("ok", questions, "jev-latest", 20000)


def test_build_request_rejects_oversized_payload():
    huge = "x" * 199_950
    questions = {
        "q": {
            "type": "choice",
            "instructions": "Pick",
            "criteria": {"a": huge},
        }
    }
    with pytest.raises(JevValidationError, match="request payload exceeds"):
        build_request("ok", questions, "jev-latest", 20000)


def test_build_request_strips_choice_criteria_and_drops_extra_keys():
    request = build_request(
        "ok",
        {
            "kind": {
                "type": "choice",
                "instructions": "Which?",
                "criteria": {" a ": " Option A "},
                "temperature": 0.9,
            }
        },
        "jev-latest",
        20000,
    )
    assert request.questions["kind"] == {
        "type": "choice",
        "instructions": "Which?",
        "criteria": {"a": "Option A"},
    }


def test_build_request_drops_extra_keys_from_noul():
    request = build_request(
        "ok",
        {"q": {"type": "noul", "instructions": "True?", "temperature": 0.5, "criteria": ["ignored"]}},
        "jev-latest",
        20000,
    )
    assert request.questions["q"] == {"type": "noul", "instructions": "True?"}
