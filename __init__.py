"""Jev integration for Hermes Agent.

This is a standalone Hermes native plugin. It intentionally has no import-time network activity;
all API calls happen only when a registered tool or slash command is invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:
    from . import schemas, tools
    from .client import PluginSettings
else:  # pragma: no cover - pytest imports the native plugin root as a plain module
    import schemas  # type: ignore[no-redef]
    import tools  # type: ignore[no-redef]
    from client import PluginSettings  # type: ignore[no-redef]


def _settings(ctx: Any) -> PluginSettings:
    return PluginSettings.from_ctx(ctx)


def register(ctx: Any) -> None:
    """Register tools, the explicit skill, and the optional ``/jev`` command."""
    settings = _settings(ctx)
    evaluate, price_assess = tools.make_handlers(settings)
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

    ctx.register_command(
        "jev",
        lambda raw_args: tools.handle_jev(evaluate, raw_args),
        description="Evaluate a JSON payload with TypeSafe Jev.",
        args_hint="<json>",
    )
