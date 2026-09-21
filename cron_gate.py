"""External, operator-invoked cron gate. Never launches tools, jobs or collectors."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import replace
from typing import Any

try:
    from .client import JevClient, PluginSettings
    from .routing import clean_text
    from .tools import _PRICE_QUESTIONS, _SENSITIVE_KEY
except ImportError:  # pragma: no cover - direct operator CLI
    from client import JevClient, PluginSettings
    from routing import clean_text
    from tools import _PRICE_QUESTIONS, _SENSITIVE_KEY

LOG = logging.getLogger("jev.cron_gate")
MAX_INPUT_CHARS = 200_000


def _redact_offer(value: Any) -> Any:
    """Redact validated JSON evidence without dropping listing details."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SENSITIVE_KEY.search(key) else _redact_offer(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_offer(item) for item in value]
    return value


def _validate(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"target", "offers"}:
        raise ValueError
    if not isinstance(payload["target"], str) or not payload["target"].strip() or len(payload["target"]) > 4096:
        raise ValueError
    offers = payload["offers"]
    if not isinstance(offers, list) or len(offers) > 32:
        raise ValueError
    identifiers = set()
    for entry in offers:
        if not isinstance(entry, dict) or set(entry) != {"id", "eligible", "offer"}:
            raise ValueError
        identifier = entry["id"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identifier)
                or identifier in identifiers or not isinstance(entry["eligible"], bool)
                or not isinstance(entry["offer"], dict) or not entry["offer"]):
            raise ValueError
        identifiers.add(identifier)
    if len(json.dumps(payload, allow_nan=False)) > MAX_INPUT_CHARS:
        raise ValueError


def assess_gate(payload: Any, settings: PluginSettings, *, mode="observe", client_factory=None,
                clock=time.monotonic) -> dict[str, Any]:
    """Eligibility is trusted collector policy; Jev cannot create eligibility."""
    try:
        _validate(payload)
        if mode not in ("off", "observe", "active"):
            raise ValueError
    except (ValueError, TypeError, OverflowError, RecursionError):
        LOG.warning("jev_gate invalid_input")
        return {"ok": False, "wakeAgent": False, "error_code": "invalid_input"}
    eligible = [entry for entry in payload["offers"] if entry["eligible"]]
    factory = client_factory or JevClient.from_settings
    candidates, would_wake = [], False
    start = clock()
    for entry in eligible:
        diagnostic: dict[str, Any] = {"id": clean_text(entry["id"])}
        if mode == "off":
            diagnostic["reason_code"] = "routing_disabled"
            wake = True
        else:
            try:
                remaining = settings.routing_budget_seconds - (clock() - start)
                if remaining < 1:
                    raise TimeoutError
                client = factory(replace(settings, timeout_seconds=min(settings.timeout_seconds, remaining)))
                result = client.evaluate(state={
                    "target_product": clean_text(payload["target"]), "offer": _redact_offer(entry["offer"]),
                    "instruction": "Assess supplied evidence only. This does not authorize a purchase or alert.",
                }, questions=_PRICE_QUESTIONS, model=settings.default_model)
                if clock() - start >= settings.routing_budget_seconds:
                    raise TimeoutError
                recommendation = result["answers"]["recommendation"]["choice"]
                if recommendation not in ("alert", "manual_review", "record", "ignore"):
                    raise ValueError
                wake = recommendation in ("alert", "manual_review")
                diagnostic["recommendation"] = recommendation
            except Exception:
                wake = True
                diagnostic["reason_code"] = "assessment_failed_review"
                LOG.warning("jev_gate assessment_failed_review")
        diagnostic["wouldWakeAgent"] = wake
        candidates.append(diagnostic)
        would_wake |= wake
    return {"ok": True, "wakeAgent": bool(eligible) if mode != "active" else would_wake,
            "wouldWakeAgent": would_wake, "candidates": candidates}


def main(argv=None, *, stdin=None, stdout=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("off", "observe", "active"), default="observe")
    parser.add_argument("--model", default="jev-latest")
    args = parser.parse_args(argv)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        raw = stdin.read(MAX_INPUT_CHARS + 1)
        payload = json.loads(raw) if len(raw) <= MAX_INPUT_CHARS else None
    except (ValueError, OSError, RecursionError):
        payload = None
    result = assess_gate(payload, PluginSettings(default_model=args.model), mode=args.mode)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), file=stdout)
    # Hermes honors wakeAgent=false only after a successful script exit.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
