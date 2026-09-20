"""Hermes tool handlers for the Jev plugin."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

try:
    from .client import (
        JevClient,
        JevConfigurationError,
        JevError,
        PluginSettings,
    )
except ImportError:  # pragma: no cover - only used when pytest imports plugin root directly
    from client import (  # type: ignore[no-redef]
        JevClient,
        JevConfigurationError,
        JevError,
        PluginSettings,
    )

_PRICE_QUESTIONS = {
    "exact_match": {
        "type": "noul",
        "instructions": (
            "Does this listing exactly match the target product, "
            "including model, capacity, generation, and variant?"
        ),
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
        "instructions": "How trustworthy is the seller or listing for a purchase decision?",
        "criteria": [
            "High risk signals, weak evidence, or suspicious seller",
            "Some uncertainty or moderate risk signals",
            "No meaningful risk signals and strong evidence",
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
        "instructions": (
            "What advisory next step best fits this evidence? "
            "This is not an authorization to buy or publish."
        ),
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


def _resolve_settings(settings: PluginSettings | Mapping[str, Any]) -> PluginSettings:
    if isinstance(settings, PluginSettings):
        return settings
    return PluginSettings.from_mapping(settings)


def _call_jev(
    client: JevClient,
    *,
    state: Any,
    questions: Mapping[str, Any],
    model: str,
    result_key: str | None = None,
    internal_message: str,
) -> str:
    try:
        response = client.evaluate(state=state, questions=questions, model=model)
        payload: dict[str, Any] = {"answers": response["answers"]}
        if "model" in response:
            payload["model"] = response["model"]
        if "usage" in response:
            payload["usage"] = response["usage"]
        if result_key is None:
            return _json({"ok": True, **payload})
        return _json({"ok": True, result_key: payload})
    except JevError as exc:
        code = "configuration" if isinstance(exc, JevConfigurationError) else "jev_error"
        return _error(code, str(exc))
    except Exception:  # noqa: BLE001 - handlers must never break Hermes' tool loop
        return _error("internal_error", internal_message)


def _evaluate(client: JevClient, settings: PluginSettings, args: Any, **_: Any) -> str:
    if not isinstance(args, Mapping):
        return _error("invalid_arguments", "tool arguments must be an object")
    questions = args.get("questions")
    if not isinstance(questions, Mapping):
        return _error("invalid_arguments", "questions must be a non-empty object")
    model = args.get("model") or settings.default_model
    return _call_jev(
        client,
        state=args.get("state"),
        questions=questions,
        model=model,
        internal_message="Jev evaluation failed unexpectedly",
    )


def _price_assess(client: JevClient, settings: PluginSettings, args: Any, **_: Any) -> str:
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
    model = args.get("model") or settings.default_model
    return _call_jev(
        client,
        state=state,
        questions=_PRICE_QUESTIONS,
        model=model,
        result_key="assessment",
        internal_message="Jev price assessment failed unexpectedly",
    )


def handle_jev(evaluate: Callable[..., str], raw_args: str) -> str:
    if not raw_args.strip():
        return _error("usage", "Use /jev with a JSON object containing state and questions.")
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return _error("invalid_json", "The /jev argument is not valid JSON.")
    return evaluate(payload)


def make_handlers(settings: PluginSettings | Mapping[str, Any]):
    """Return closures so Hermes config is resolved once per plugin registration."""
    plugin_settings = _resolve_settings(settings)
    client = JevClient.from_settings(plugin_settings)

    def evaluate(args: Any, **kwargs: Any) -> str:
        return _evaluate(client, plugin_settings, args, **kwargs)

    def price_assess(args: Any, **kwargs: Any) -> str:
        return _price_assess(client, plugin_settings, args, **kwargs)

    return evaluate, price_assess
