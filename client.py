"""Small, dependency-free client for TypeSafe's Jev System One API.

The plugin intentionally uses the documented HTTP endpoint instead of importing a vendor SDK.
That keeps installation light and makes the request/response contract explicit and testable.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_API_KEY_ENV = "TYPESAFE_API_KEY"
_QUESTION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ALLOWED_TYPES = {"noul", "choice", "score"}


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
            text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise JevValidationError("state must contain only JSON-compatible values") from exc
    else:
        raise JevValidationError("state must be a string, object, or array")

    if not text.strip():
        raise JevValidationError("state must not be empty")
    if len(text) > max_chars:
        raise JevValidationError(f"state exceeds the configured limit of {max_chars} characters")
    return text


def _validate_question(name: str, raw: Any) -> dict[str, Any]:
    if not _QUESTION_NAME.fullmatch(name):
        raise JevValidationError(f"invalid question name: {name!r}")
    if not isinstance(raw, Mapping):
        raise JevValidationError(f"question {name!r} must be an object")

    question = dict(raw)
    kind = question.get("type")
    if not isinstance(kind, str) or kind.lower() not in _ALLOWED_TYPES:
        raise JevValidationError(f"question {name!r} type must be noul, choice, or score")
    question["type"] = kind.lower()

    instructions = question.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise JevValidationError(f"question {name!r} needs non-empty instructions")
    question["instructions"] = instructions.strip()

    if question["type"] == "choice":
        criteria = question.get("criteria")
        if not isinstance(criteria, Mapping) or len(criteria) < 1:
            raise JevValidationError(f"choice question {name!r} needs a non-empty criteria object")
        if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
               for k, v in criteria.items()):
            raise JevValidationError(f"choice question {name!r} criteria must map strings to descriptions")
        question["criteria"] = dict(criteria)
    elif question["type"] == "score":
        criteria = question.get("criteria")
        if not isinstance(criteria, list) or len(criteria) < 2 or any(
            not isinstance(item, str) or not item.strip() for item in criteria
        ):
            raise JevValidationError(f"score question {name!r} needs at least two non-empty criteria")
        question["criteria"] = [item.strip() for item in criteria]

    return question


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
    return JevRequest(
        state=_serialized_state(state, max_state_chars),
        model=model.strip(),
        questions=normalized,
    )


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
            self.timeout = max(1.0, min(float(timeout), 600.0))
            self.max_state_chars = max(256, min(int(max_state_chars), 200_000))
        except (TypeError, ValueError) as exc:
            raise JevConfigurationError("timeout and max_state_chars must be numeric") from exc
        self._opener = opener or urlopen

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
        body = json.dumps(request.as_payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        http_request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._resolved_key()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "jev-plugin-for-hermes/0.1.0",
            },
        )

        try:
            with self._opener(http_request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw = response.read()
        except HTTPError as exc:
            raise JevHTTPError(int(exc.code)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise JevTransportError("could not reach the Jev API") from exc

        if status < 200 or status >= 300:
            raise JevHTTPError(status)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevProtocolError("Jev API returned invalid JSON") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("answers"), dict):
            raise JevProtocolError("Jev API response did not contain an answers object")
        return decoded
