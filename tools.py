"""Hermes tool handlers for the Jev plugin."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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

_TOOL_CALL_REASON_CODES = {
    "no_issue": "No blocking concern; the supplied preview is clear enough for the call to continue.",
    "insufficient_context": "The preview does not contain enough evidence to assess the call safely.",
    "sensitive_data": "The call includes or may expose credentials, personal data, or other sensitive information.",
    "external_side_effect": "The call may modify an external system or durable state.",
    "destructive_change": "The call may delete, overwrite, reset, or otherwise irreversibly alter data.",
    "deployment_or_release": "The call may build, deploy, publish, restart, or release software or services.",
    "human_confirmation": "The call requires explicit human confirmation before execution.",
    "policy_violation": "The call conflicts with the supplied safety policy or cannot execute under the evidence.",
}

_TOOL_CALL_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": (
            "How should this tool call be handled? Assess only the supplied tool name, "
            "sanitized arguments, and any local_file_excerpts. Do not infer facts that "
            "are not present in the preview. Use allow when the evidence shows a local, "
            "reversible action such as reading files, git status/diff, or running a test "
            "harness (*_test.sh, pytest, unittest) whose excerpt does not contact production. "
            "A path whose name contains deploy is not a production deploy if an attached "
            "excerpt shows a local test harness. Use review for remaining ambiguity, a real "
            "deploy/release, or any call that should require a human confirmation. Use deny "
            "when the call should not execute under the supplied evidence."
        ),
        "criteria": {
            "allow": "The call is sufficiently clear and does not need an extra human confirmation",
            "review": "The call is ambiguous, sensitive, or should be confirmed by a human",
            "deny": "The call should not execute under the supplied evidence",
        },
    },
    "reason_code": {
        "type": "choice",
        "instructions": (
            "Select the single primary reason code for the action decision. Use no_issue for "
            "allow. For review or deny, select the most specific applicable code. Assess only "
            "the supplied preview and do not invent facts or a free-form explanation."
        ),
        "criteria": _TOOL_CALL_REASON_CODES,
    },
}

_SENSITIVE_KEY = re.compile(
    r"(?:password|passphrase|secret|token|api[_-]?key|authorization|cookie|credential|"
    r"private[_-]?key|card(?:[_-]?number)?|cvc|cvv)",
    re.IGNORECASE,
)
_SECRET_TEXT = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|authorization)\b\s*[:=]\s*['\"]?[^,\s'\"]+"
    ),
    re.compile(r"\b(?:sk|pk|ghp|github_pat|xox[baprs]-)[A-Za-z0-9_-]+"),
)
_MAX_PREVIEW_DEPTH = 4
_MAX_PREVIEW_ITEMS = 64
_MAX_PREVIEW_TEXT = 512
_MAX_PREVIEW_STATE_CHARS = 12_000
_MAX_FILE_EXCERPT_CHARS = 4_000
_MAX_ATTACHED_FILES = 2
_MAX_ATTACHED_FILE_BYTES = 64_000
_ATTACHABLE_SUFFIXES = {".sh", ".bash", ".zsh", ".py"}
_SKIP_FILE_NAME = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:\..*)?"
    r"|.*\.(?:pem|key|p12|pfx|jks)"
    r"|id_rsa|id_ed25519|id_dsa"
    r"|credentials(?:\.json)?"
    r"|google-services\.json"
    r"|auth\.json"
    r"|.*secret.*"
    r"|.*password.*"
    r")$",
    re.IGNORECASE,
)


def _redact_text(value: str) -> str:
    """Keep tool-call evidence useful while removing common inline credentials."""
    text = value
    for pattern in _SECRET_TEXT:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > _MAX_PREVIEW_TEXT:
        return text[:_MAX_PREVIEW_TEXT] + f"… [truncated, {len(text)} chars total]"
    return text


def _redact_excerpt(value: str) -> str:
    text = value
    for pattern in _SECRET_TEXT:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > _MAX_FILE_EXCERPT_CHARS:
        return text[:_MAX_FILE_EXCERPT_CHARS] + f"… [truncated, {len(text)} chars total]"
    return text


def normalize_hook_cwd(value: Any) -> str:
    """Return a filesystem cwd from a Codex/Hermes hook event field."""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.startswith("file://"):
        parsed = urlparse(text)
        if parsed.scheme != "file":
            return ""
        return unquote(parsed.path)
    return text


def _preview_args(args: Any) -> Mapping[str, Any] | dict[str, Any]:
    if isinstance(args, Mapping):
        return args
    if isinstance(args, str):
        return {"command": args}
    return {}


def _command_from_args(args: Any) -> str:
    preview = _preview_args(args)
    for key in ("command", "cmd"):
        value = preview.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _looks_like_test(relative_path: str) -> bool:
    name = relative_path.replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    return (
        "/test/" in f"/{name}/"
        or "/tests/" in f"/{name}/"
        or base.startswith("test_")
        or "_test." in base
        or base.endswith("_test")
    )


def _resolve_under_cwd(token: str, cwd: Path) -> Path | None:
    if not token or token.startswith("-") or "://" in token:
        return None
    try:
        cwd_resolved = cwd.resolve()
        raw = Path(token)
        candidate = raw if raw.is_absolute() else cwd_resolved / raw
        resolved = candidate.resolve()
        resolved.relative_to(cwd_resolved)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _local_file_excerpts(args: Any, cwd: str) -> list[dict[str, Any]]:
    root = normalize_hook_cwd(cwd)
    if not root:
        return []
    cwd_path = Path(root)
    if not cwd_path.is_dir():
        return []

    excerpts: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for token in _command_tokens(_command_from_args(args)):
        if len(excerpts) >= _MAX_ATTACHED_FILES:
            break
        resolved = _resolve_under_cwd(token, cwd_path)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        relative = resolved.relative_to(cwd_path.resolve()).as_posix()
        if _SKIP_FILE_NAME.search(relative) or _SENSITIVE_KEY.search(relative):
            continue
        if resolved.suffix.lower() not in _ATTACHABLE_SUFFIXES:
            continue
        try:
            if not resolved.is_file() or resolved.stat().st_size > _MAX_ATTACHED_FILE_BYTES:
                continue
            with resolved.open(encoding="utf-8") as handle:
                text = handle.read(_MAX_FILE_EXCERPT_CHARS + 1)
        except (OSError, UnicodeError):
            continue
        excerpts.append(
            {
                "path": relative,
                "looks_like_test": _looks_like_test(relative),
                "excerpt": _redact_excerpt(text),
            }
        )
    return excerpts


def _sanitize_tool_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Build a bounded, JSON-compatible preview for the external Jev request.

    Tool arguments can contain credentials or user documents. Sensitive-looking keys are
    removed entirely from the preview; other strings are pattern-redacted and truncated.
    """
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if depth >= _MAX_PREVIEW_DEPTH:
        return f"<{type(value).__name__} omitted at preview depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        items = list(value.items())[:_MAX_PREVIEW_ITEMS]
        result = {
            str(item_key): _sanitize_tool_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items
        }
        if len(value) > _MAX_PREVIEW_ITEMS:
            result["[truncated_keys]"] = len(value) - _MAX_PREVIEW_ITEMS
        return result
    if isinstance(value, (list, tuple)):
        result = [_sanitize_tool_value(item, depth=depth + 1) for item in value[:_MAX_PREVIEW_ITEMS]]
        if len(value) > _MAX_PREVIEW_ITEMS:
            result.append(f"[truncated_items: {len(value) - _MAX_PREVIEW_ITEMS}]")
        return result
    return f"<{type(value).__name__}>"


def _tool_call_state(
    tool_name: str,
    args: Any,
    *,
    cwd: str = "",
    attach_local_files: bool = False,
) -> dict[str, Any]:
    preview = _sanitize_tool_value(_preview_args(args))
    serialized = json.dumps(preview, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(serialized) > _MAX_PREVIEW_STATE_CHARS:
        preview = {
            "[preview_truncated]": True,
            "serialized_preview_prefix": serialized[: _MAX_PREVIEW_STATE_CHARS - 512],
            "original_preview_chars": len(serialized),
        }
    state: dict[str, Any] = {
        "tool_name": tool_name,
        "arguments": preview,
        "instruction": (
            "Assess this call before execution. Use local_file_excerpts when present. "
            "Do not infer facts not present in the preview."
        ),
    }
    if attach_local_files:
        excerpts = _local_file_excerpts(args, cwd)
        if excerpts:
            state["local_file_excerpts"] = excerpts
    return state


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


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


def _tool_call_guard(
    client: JevClient,
    settings: PluginSettings,
    tool_name: str,
    args: Any,
    *,
    cwd: str = "",
    attach_local_files: bool = False,
) -> dict[str, str] | None:
    """Assess every tool call without giving Jev unilateral execution authority.

    ``allow`` leaves the normal Hermes policy/approval path untouched. ``review`` and ``deny``
    block the call before execution; this preserves the first-directive-wins semantics of
    Hermes' hook dispatcher and cannot bypass another plugin's stronger block. A Jev outage
    also blocks the call fail-closed.
    """
    try:
        response = client.evaluate(
            state=_tool_call_state(
                tool_name,
                args,
                cwd=cwd,
                attach_local_files=attach_local_files,
            ),
            questions=_TOOL_CALL_QUESTIONS,
            model=settings.default_model,
        )
        answers = response["answers"]
        action = answers["action"]["choice"]
        reason_code = answers["reason_code"]["choice"]
    except JevError:
        return {
            "action": "block",
            "message": "Jev pre-tool assessment failed; the tool call was blocked.",
        }
    except Exception:  # noqa: BLE001 - a hook must never break the agent loop
        return {
            "action": "block",
            "message": "Jev pre-tool assessment failed unexpectedly; the tool call was blocked.",
        }

    if action == "allow" and reason_code == "no_issue":
        return None
    if action in {"review", "deny"} and reason_code != "no_issue":
        reason = _TOOL_CALL_REASON_CODES.get(reason_code)
        if reason is None:
            return {
                "action": "block",
                "message": (
                    f"Jev blocked tool '{tool_name}' before execution ({action}); "
                    "reason: invalid"
                ),
            }
        return {
            "action": "block",
            "message": (
                f"Jev blocked tool '{tool_name}' before execution ({action}); "
                f"reason: {reason_code} — {reason}"
            ),
        }
    return {
        "action": "block",
        "message": "Jev returned an invalid pre-tool decision; the tool call was blocked.",
    }


_HOOK_TIMEOUT_MAX = 25.0


def make_tool_call_guard(
    settings: PluginSettings | Mapping[str, Any],
    *,
    attach_local_files: bool = False,
):
    """Return the ``pre_tool_call`` callback used for all Hermes tool calls."""
    plugin_settings = _resolve_settings(settings)
    hook_timeout = max(1.0, min(plugin_settings.timeout_seconds, _HOOK_TIMEOUT_MAX))
    hook_settings = replace(plugin_settings, timeout_seconds=hook_timeout)
    client = JevClient.from_settings(hook_settings)

    def guard(tool_name: str = "", args: Any = None, cwd: str = "", **_: Any) -> dict[str, str] | None:
        if not tool_name:
            return {
                "action": "block",
                "message": "Jev could not identify the tool call; the tool call was blocked.",
            }
        return _tool_call_guard(
            client,
            hook_settings,
            tool_name,
            args,
            cwd=cwd,
            attach_local_files=attach_local_files,
        )

    return guard


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
