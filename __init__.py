"""Jev integration for Hermes Agent.

This is a standalone Hermes native plugin. It intentionally has no import-time network activity;
API calls happen only in invoked tools, slash commands, or enabled runtime callbacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:
    from . import schemas, tools
    from .client import PluginSettings
    from .routing import register_routing
else:  # pragma: no cover - pytest imports the native plugin root as a plain module
    import schemas  # type: ignore[no-redef]
    import tools  # type: ignore[no-redef]
    from client import PluginSettings  # type: ignore[no-redef]
    from routing import register_routing


def _settings(ctx: Any) -> PluginSettings:
    return PluginSettings.from_ctx(ctx)


def register(ctx: Any) -> None:
    """Register tools, skill, command, guard, and configured advisory callbacks."""
    settings = _settings(ctx)
    evaluate, price_assess = tools.make_handlers(settings)
    tool_call_guard = tools.make_tool_call_guard(settings)
    ctx.register_tool(
        name="jev_evaluate",
        toolset="jev",
        schema=schemas.JEV_EVALUATE,
        handler=evaluate,
        description=schemas.JEV_EVALUATE["description"],
    )
    ctx.register_tool(
        name="jev_price_assess",
        toolset="jev",
        schema=schemas.JEV_PRICE_ASSESS,
        handler=price_assess,
        description=schemas.JEV_PRICE_ASSESS["description"],
    )
    ctx.register_hook("pre_tool_call", tool_call_guard)
    register_routing(ctx, settings)

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
