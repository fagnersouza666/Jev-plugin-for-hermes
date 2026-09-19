"""Hermes tool handlers for the Jev plugin."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

try:
    from .client import (
        DEFAULT_ENDPOINT,
        DEFAULT_MODEL,
        JevClient,
        JevConfigurationError,
        JevError,
    )
except ImportError:  # pragma: no cover - only used when pytest imports plugin root directly
    from client import (  # type: ignore[no-redef]
        DEFAULT_ENDPOINT,
        DEFAULT_MODEL,
        JevClient,
        JevConfigurationError,
        JevError,
    )

_PRICE_QUESTIONS = {
    "exact_match": {
        "type": "noul",
        "instructions": "Does this listing exactly match the target product, including model, capacity, generation, and variant?",
    },
    "condition": {
        "type": "choice",
        "instructions": "What condition is established by the listing evidence?",
        "criteria": {
            "new": "New, sealed, or explicitly factory-new",
            "open_box": "Open box or refurbished but not normally used",
            "used": "Used, visibly worn, or previously owned",
            "unknown": "Condition is missing, contradictory, or cannot be established",
        },
    },
    "seller_risk": {
        "type": "score",
        "instructions": "How risky is the seller or listing for a purchase decision?",
        "criteria": [
            "No meaningful risk signals and strong evidence",
            "Some uncertainty or moderate risk signals",
            "High risk signals, weak evidence, or suspicious seller",
        ],
    },
    "deal_quality": {
        "type": "score",
        "instructions": "How attractive is the offer after considering price, shipping, condition, and fit?",
        "criteria": [
            "Invalid or unattractive offer",
            "Usable but weak offer",
            "Good offer",
            "Exceptional offer",
        ],
    },
    "recommendation": {
        "type": "choice",
        "instructions": "What advisory next step best fits this evidence? This is not an authorization to buy or publish.",
        "criteria": {
            "ignore": "Do not retain as a candidate",
            "record": "Record for history or later comparison",
            "manual_review": "Ask for human review because evidence or risk is uncertain",
            "alert": "Surface as a candidate alert after deterministic policy checks",
        },
    },
}


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _error(code: str, message: str, **extra: Any) -> str:
    result: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    result.update(extra)
    return _json(result)


def _client(settings: Mapping[str, Any]) -> JevClient:
    endpoint = settings.get("api_url", DEFAULT_ENDPOINT)
    model = settings.get("default_model", DEFAULT_MODEL)
    try:
        timeout = float(settings.get("timeout_seconds", 30.0))
        max_state_chars = int(settings.get("max_state_chars", 20_000))
    except (TypeError, ValueError) as exc:
        raise JevConfigurationError("plugin settings timeout_seconds and max_state_chars must be numeric") from exc
    if not isinstance(endpoint, str) or not isinstance(model, str):
        raise JevConfigurationError("plugin settings api_url and default_model must be strings")
    return JevClient(
        endpoint=endpoint,
        timeout=timeout,
        max_state_chars=max_state_chars,
    )


def _evaluate(settings: Mapping[str, Any], args: Any, **_: Any) -> str:
    if not isinstance(args, Mapping):
        return _error("invalid_arguments", "tool arguments must be an object")
    questions = args.get("questions")
    if not isinstance(questions, Mapping):
        return _error("invalid_arguments", "questions must be a non-empty object")
    try:
        model = args.get("model") or settings.get("default_model", DEFAULT_MODEL)
        response = _client(settings).evaluate(
            state=args.get("state"),
            questions=questions,
            model=model,
        )
        return _json({"ok": True, **response})
    except JevError as exc:
        code = "configuration" if isinstance(exc, JevConfigurationError) else "jev_error"
        return _error(code, str(exc))
    except Exception:  # noqa: BLE001 - handlers must never break Hermes' tool loop
        # Tool handlers must never break the agent loop. Do not echo arbitrary exception text: it can
        # contain request data or secrets from a third-party library.
        return _error("internal_error", "Jev evaluation failed unexpectedly")


def _price_assess(settings: Mapping[str, Any], args: Any, **_: Any) -> str:
    if not isinstance(args, Mapping):
        return _error("invalid_arguments", "tool arguments must be an object")
    target = args.get("target")
    offer = args.get("offer")
    if not isinstance(target, str) or not target.strip():
        return _error("invalid_arguments", "target must be a non-empty string")
    if not isinstance(offer, Mapping) or not offer:
        return _error("invalid_arguments", "offer must be a non-empty object")

    state = {
        "target_product": target.strip(),
        "offer": dict(offer),
        "instruction": "Assess only from the supplied evidence. Do not invent missing fields.",
    }
    try:
        model = args.get("model") or settings.get("default_model", DEFAULT_MODEL)
        response = _client(settings).evaluate(
            state=state,
            questions=_PRICE_QUESTIONS,
            model=model,
        )
        return _json({"ok": True, "assessment": response})
    except JevError as exc:
        code = "configuration" if isinstance(exc, JevConfigurationError) else "jev_error"
        return _error(code, str(exc))
    except Exception:  # noqa: BLE001 - handlers must never break Hermes' tool loop
        return _error("internal_error", "Jev price assessment failed unexpectedly")


def make_handlers(settings: Mapping[str, Any]):
    """Return closures so Hermes config is resolved once per plugin registration."""
    frozen_settings = dict(settings)

    def evaluate(args: Any, **kwargs: Any) -> str:
        return _evaluate(frozen_settings, args, **kwargs)

    def price_assess(args: Any, **kwargs: Any) -> str:
        return _price_assess(frozen_settings, args, **kwargs)

    return evaluate, price_assess
