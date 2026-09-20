"""Small, dependency-free client for TypeSafe's Jev System One API.

The plugin intentionally uses the documented HTTP endpoint instead of importing a vendor SDK.
That keeps installation light and makes the request/response contract explicit and testable.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_API_KEY_ENV = "TYPESAFE_API_KEY"
PLUGIN_VERSION = "0.1.0"
_QUESTION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ALLOWED_TYPES = {"noul", "choice", "score"}
_TIMEOUT_MIN = 1.0
_TIMEOUT_MAX = 600.0
_STATE_CHARS_MIN = 256
_STATE_CHARS_MAX = 200_000
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_QUESTIONS = 32
_JSON_DUMP_KWARGS = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":"), "allow_nan": False}


class JevError(Exception):
    """Base class for expected plugin/API failures."""


class JevConfigurationError(JevError):
    """The local configuration cannot make a safe API request."""


class JevValidationError(JevError):
    """The tool arguments do not satisfy the Jev request contract."""


class JevTransportError(JevError):
    """The request could not be completed."""


class JevHTTPError(JevError):
    """The API returned a non-success HTTP status."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Jev API returned HTTP {status}")


class JevProtocolError(JevError):
    """The API response was not valid JSON in the expected shape."""


@dataclass(frozen=True)
class PluginSettings:
    """Resolved Hermes plugin settings with defaults and clamps applied once."""

    api_url: str = DEFAULT_ENDPOINT
    default_model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    max_state_chars: int = 20_000

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None = None) -> PluginSettings:
        data = mapping or {}
        try:
            timeout = float(data.get("timeout_seconds", 30.0))
            max_state_chars = int(data.get("max_state_chars", 20_000))
        except (TypeError, ValueError) as exc:
            raise JevConfigurationError("plugin settings timeout_seconds and max_state_chars must be numeric") from exc

        api_url = data.get("api_url", DEFAULT_ENDPOINT)
        default_model = data.get("default_model", DEFAULT_MODEL)
        if not isinstance(api_url, str) or not isinstance(default_model, str):
            raise JevConfigurationError("plugin settings api_url and default_model must be strings")

        return cls(
            api_url=api_url,
            default_model=default_model,
            timeout_seconds=max(_TIMEOUT_MIN, min(timeout, _TIMEOUT_MAX)),
            max_state_chars=max(_STATE_CHARS_MIN, min(max_state_chars, _STATE_CHARS_MAX)),
        )

    @classmethod
    def from_ctx(cls, ctx: Any) -> PluginSettings:
        return cls.from_mapping(
            {
                "api_url": ctx.get_config("api_url", DEFAULT_ENDPOINT),
                "default_model": ctx.get_config("default_model", DEFAULT_MODEL),
                "timeout_seconds": ctx.get_config("timeout_seconds", 30.0),
                "max_state_chars": ctx.get_config("max_state_chars", 20_000),
            }
        )


@dataclass(frozen=True)
class JevRequest:
    """Validated request payload, useful for tests and diagnostics without secrets."""

    state: str
    model: str
    questions: dict[str, dict[str, Any]]

    def as_payload(self) -> dict[str, Any]:
        return {"state": self.state, "model": self.model, "questions": self.questions}


def _serialized_state(state: Any, max_chars: int) -> str:
    if isinstance(state, str):
        text = state
    elif isinstance(state, (Mapping, list)):
        try:
            text = json.dumps(state, **_JSON_DUMP_KWARGS)
        except (TypeError, ValueError) as exc:
            raise JevValidationError("state must contain only JSON-compatible values") from exc
    else:
        raise JevValidationError("state must be a string, object, or array")

    if not text.strip():
        raise JevValidationError("state must not be empty")
    if len(text) > max_chars:
        raise JevValidationError(f"state exceeds the configured limit of {max_chars} characters")
    return text


def _validate_choice_criteria(question: dict[str, Any], name: str) -> dict[str, str]:
    criteria = question.get("criteria")
    if not isinstance(criteria, Mapping) or len(criteria) < 1:
        raise JevValidationError(f"choice question {name!r} needs a non-empty criteria object")
    if any(
        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
        for k, v in criteria.items()
    ):
        raise JevValidationError(f"choice question {name!r} criteria must map strings to descriptions")
    return {k.strip(): v.strip() for k, v in criteria.items()}


def _validate_score_criteria(question: dict[str, Any], name: str) -> list[str]:
    criteria = question.get("criteria")
    if not isinstance(criteria, list) or len(criteria) < 2 or any(
        not isinstance(item, str) or not item.strip() for item in criteria
    ):
        raise JevValidationError(f"score question {name!r} needs at least two non-empty criteria")
    return [item.strip() for item in criteria]


def _validate_question(name: str, raw: Any) -> dict[str, Any]:
    if not _QUESTION_NAME.fullmatch(name):
        raise JevValidationError(f"invalid question name: {name!r}")
    if not isinstance(raw, Mapping):
        raise JevValidationError(f"question {name!r} must be an object")

    kind = raw.get("type")
    if not isinstance(kind, str) or kind.lower() not in _ALLOWED_TYPES:
        raise JevValidationError(f"question {name!r} type must be noul, choice, or score")
    kind = kind.lower()

    instructions = raw.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise JevValidationError(f"question {name!r} needs non-empty instructions")

    out: dict[str, Any] = {
        "type": kind,
        "instructions": instructions.strip(),
    }
    if kind == "choice":
        out["criteria"] = _validate_choice_criteria(raw, name)
    elif kind == "score":
        out["criteria"] = _validate_score_criteria(raw, name)

    return out


def build_request(state: Any, questions: Mapping[str, Any], model: str, max_state_chars: int) -> JevRequest:
    if not isinstance(questions, Mapping) or not questions:
        raise JevValidationError("questions must be a non-empty object")
    if not isinstance(model, str) or not model.strip():
        raise JevValidationError("model must be a non-empty string")
    normalized = {
        name: _validate_question(name, raw)
        for name, raw in questions.items()
        if isinstance(name, str)
    }
    if len(normalized) != len(questions):
        raise JevValidationError("question names must be strings")
    if len(normalized) > _MAX_QUESTIONS:
        raise JevValidationError(f"questions must contain at most {_MAX_QUESTIONS} entries")
    request = JevRequest(
        state=_serialized_state(state, max_state_chars),
        model=model.strip(),
        questions=normalized,
    )
    try:
        payload_size = len(json.dumps(request.as_payload(), **_JSON_DUMP_KWARGS))
    except (TypeError, ValueError) as exc:
        raise JevValidationError("request payload must contain only JSON-compatible values") from exc
    if payload_size > _STATE_CHARS_MAX:
        raise JevValidationError(f"request payload exceeds the limit of {_STATE_CHARS_MAX} characters")
    return request


def _validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise JevConfigurationError("api_url must be a non-empty URL")
    parsed = urlparse(endpoint.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise JevConfigurationError("api_url must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise JevConfigurationError("api_url must not contain credentials, query parameters, or fragments")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise JevConfigurationError("api_url must use HTTPS except for local test endpoints")
    return endpoint.strip()


def _clamp_timeout(timeout: float) -> float:
    return max(_TIMEOUT_MIN, min(float(timeout), _TIMEOUT_MAX))


def _clamp_max_state_chars(max_state_chars: int) -> int:
    return max(_STATE_CHARS_MIN, min(int(max_state_chars), _STATE_CHARS_MAX))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener() -> Callable[..., Any]:
    return urllib.request.build_opener(_NoRedirect).open


def _read_limited(response: Any, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise JevProtocolError("Jev API response exceeded the size limit")
    return data


def _close_http_error(exc: HTTPError) -> None:
    status = int(exc.code)
    try:
        if exc.fp is not None:
            exc.fp.read(_MAX_RESPONSE_BYTES + 1)
    finally:
        exc.close()
    raise JevHTTPError(status) from exc


def _post_json(
    opener: Callable[..., Any],
    *,
    endpoint: str,
    timeout: float,
    body: bytes,
    api_key: str,
) -> tuple[int, bytes]:
    http_request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"jev-plugin-for-hermes/{PLUGIN_VERSION}",
        },
    )
    try:
        with opener(http_request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = _read_limited(response, _MAX_RESPONSE_BYTES)
    except HTTPError as exc:
        _close_http_error(exc)
    except (URLError, TimeoutError, OSError) as exc:
        raise JevTransportError("could not reach the Jev API") from exc
    return status, raw


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _validate_answer_body(name: str, body: Any, expected_question: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise JevProtocolError("Jev API response did not contain an answers object")

    kind = expected_question["type"]
    if body.get("type") != kind:
        raise JevProtocolError("Jev API response did not contain an answers object")

    if kind == "noul":
        if set(body.keys()) != {"type", "noul"}:
            raise JevProtocolError("Jev API response did not contain an answers object")
        value = body["noul"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise JevProtocolError("Jev API response did not contain an answers object")
        if not math.isfinite(value) or value < 0 or value > 1:
            raise JevProtocolError("Jev API response did not contain an answers object")
        return {"type": "noul", "noul": value}

    if kind == "choice":
        if set(body.keys()) != {"type", "choice"}:
            raise JevProtocolError("Jev API response did not contain an answers object")
        criteria = expected_question["criteria"]
        choice = body["choice"]
        if not isinstance(choice, str) or choice not in criteria:
            raise JevProtocolError("Jev API response did not contain an answers object")
        return {"type": "choice", "choice": choice}

    if kind == "score":
        if set(body.keys()) != {"type", "score"}:
            raise JevProtocolError("Jev API response did not contain an answers object")
        rubric = expected_question["criteria"]
        value = body["score"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise JevProtocolError("Jev API response did not contain an answers object")
        if not math.isfinite(value) or value < 0 or value > len(rubric) - 1:
            raise JevProtocolError("Jev API response did not contain an answers object")
        return {"type": "score", "score": value}

    raise JevProtocolError("Jev API response did not contain an answers object")


def _parse_answers(raw: bytes, status: int, expected: Mapping[str, Any]) -> dict[str, Any]:
    if status < 200 or status >= 300:
        raise JevHTTPError(status)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise JevProtocolError("Jev API response exceeded the size limit")
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise JevProtocolError("Jev API returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise JevProtocolError("Jev API response did not contain an answers object")

    answers = decoded.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(expected):
        raise JevProtocolError("Jev API response did not contain an answers object")

    validated_answers = {
        name: _validate_answer_body(name, body, expected[name]) for name, body in answers.items()
    }
    result: dict[str, Any] = {"answers": validated_answers}

    if "model" in decoded:
        model = decoded["model"]
        if not isinstance(model, str) or not model.strip():
            raise JevProtocolError("Jev API response did not contain an answers object")
        result["model"] = model.strip()

    if "usage" in decoded:
        usage = decoded["usage"]
        if not isinstance(usage, dict):
            raise JevProtocolError("Jev API response did not contain an answers object")
        try:
            json.dumps(usage, **_JSON_DUMP_KWARGS)
        except (TypeError, ValueError) as exc:
            raise JevProtocolError("Jev API response did not contain an answers object") from exc
        result["usage"] = usage

    try:
        json.dumps(result, **_JSON_DUMP_KWARGS)
    except (TypeError, ValueError) as exc:
        raise JevProtocolError("Jev API response did not contain an answers object") from exc

    return result


class JevClient:
    """Synchronous Jev client with injectable transport for deterministic tests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 30.0,
        max_state_chars: int = 20_000,
        opener: Callable[..., Any] | None = None,
    ):
        self._api_key = api_key
        self.api_key_env = api_key_env
        self.endpoint = _validate_endpoint(endpoint)
        try:
            self.timeout = _clamp_timeout(timeout)
            self.max_state_chars = _clamp_max_state_chars(max_state_chars)
        except (TypeError, ValueError) as exc:
            raise JevConfigurationError("timeout and max_state_chars must be numeric") from exc
        self._opener = opener or _default_opener()

    @classmethod
    def from_settings(
        cls,
        settings: PluginSettings,
        *,
        api_key: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> JevClient:
        return cls(
            api_key=api_key,
            endpoint=settings.api_url,
            timeout=settings.timeout_seconds,
            max_state_chars=settings.max_state_chars,
            opener=opener,
        )

    def _resolved_key(self) -> str:
        key = self._api_key if self._api_key is not None else os.getenv(self.api_key_env)
        if not isinstance(key, str) or not key.strip():
            raise JevConfigurationError(f"missing {self.api_key_env}; configure it in Hermes' secret environment")
        return key.strip()

    def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Any],
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        request = build_request(state, questions, model, self.max_state_chars)
        body = json.dumps(request.as_payload(), **_JSON_DUMP_KWARGS).encode("utf-8")
        status, raw = _post_json(
            self._opener,
            endpoint=self.endpoint,
            timeout=self.timeout,
            body=body,
            api_key=self._resolved_key(),
        )
        return _parse_answers(raw, status, request.questions)
