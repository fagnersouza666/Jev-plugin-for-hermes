"""Jev integration for Hermes Agent.

This is a standalone Hermes native plugin. It intentionally has no import-time network activity;
all API calls happen only when a registered tool or slash command is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from . import schemas, tools
else:  # pragma: no cover - pytest imports the native plugin root as a plain module
    import schemas  # type: ignore[no-redef]
    import tools  # type: ignore[no-redef]


def _settings(ctx: Any) -> dict[str, Any]:
    return {
        "api_url": ctx.get_config("api_url", "https://api.typesafe.ai/v1/systemone"),
        "default_model": ctx.get_config("default_model", "jev-latest"),
        "timeout_seconds": ctx.get_config("timeout_seconds", 30.0),
        "max_state_chars": ctx.get_config("max_state_chars", 20_000),
    }


def register(ctx: Any) -> None:
    """Register tools, the explicit skill, and the optional ``/jev`` command."""
    evaluate, price_assess = tools.make_handlers(_settings(ctx))
    ctx.register_tool(
        name="jev_evaluate",
        toolset="jev",
        schema=schemas.JEV_EVALUATE,
        handler=evaluate,
        description="Evaluate evidence with TypeSafe Jev primitives.",
    )
    ctx.register_tool(
        name="jev_price_assess",
        toolset="jev",
        schema=schemas.JEV_PRICE_ASSESS,
        handler=price_assess,
        description="Assess a product offer with Jev; advisory only.",
    )

    skill_path = Path(__file__).parent / "skills" / "jev-playbook" / "SKILL.md"
    ctx.register_skill(
        "jev-playbook",
        skill_path,
        description="Use Jev for typed decisions, confidence checks, and safe routing.",
    )

    def handle_jev(raw_args: str) -> str:
        if not raw_args.strip():
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "usage",
                        "message": "Use /jev with a JSON object containing state and questions.",
                    },
                },
                ensure_ascii=False,
            )
        try:
            payload = json.loads(raw_args)
        except json.JSONDecodeError:
            return json.dumps(
                {"ok": False, "error": {"code": "invalid_json", "message": "The /jev argument is not valid JSON."}},
                ensure_ascii=False,
            )
        return evaluate(payload)

    ctx.register_command(
        "jev",
        handle_jev,
        description="Evaluate a JSON payload with TypeSafe Jev.",
        args_hint="<json>",
    )
