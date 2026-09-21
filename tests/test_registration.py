import json
import re
from pathlib import Path

import jev_plugin_for_hermes as plugin
import pytest
from jev_plugin_for_hermes import schemas
from jev_plugin_for_hermes.client import PLUGIN_VERSION


class FakeContext:
    def __init__(self, config=None):
        self.tools = {}
        self.skills = {}
        self.commands = {}
        self.hooks = {}
        self.middleware = {}
        self.config = config or {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def register_skill(self, name, path, description="", **kwargs):
        self.skills[name] = {"path": Path(path), "description": description}

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback


def test_plugin_version_matches_manifest_and_pyproject():
    root = Path(__file__).resolve().parents[1]
    plugin_yaml = (root / "plugin.yaml").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    skill_md = (root / "skills" / "jev-playbook" / "SKILL.md").read_text(encoding="utf-8")
    yaml_version = re.search(r"^version:\s*(\S+)", plugin_yaml, re.MULTILINE)
    toml_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    skill_version = re.search(r"^version:\s*(\S+)", skill_md, re.MULTILINE)
    assert yaml_version is not None
    assert toml_version is not None
    assert skill_version is not None
    assert PLUGIN_VERSION == yaml_version.group(1) == toml_version.group(1) == skill_version.group(1)


def test_registers_tools_skill_and_command():
    ctx = FakeContext()
    plugin.register(ctx)

    assert set(ctx.tools) == {"jev_evaluate", "jev_price_assess"}
    assert ctx.tools["jev_evaluate"]["toolset"] == "jev"
    assert ctx.tools["jev_evaluate"]["description"] == schemas.JEV_EVALUATE["description"]
    assert ctx.tools["jev_price_assess"]["description"] == schemas.JEV_PRICE_ASSESS["description"]
    assert ctx.skills["jev-playbook"]["path"].is_file()
    assert "jev" in ctx.commands
    assert "pre_tool_call" in ctx.hooks

    usage = json.loads(ctx.commands["jev"](""))
    assert usage["error"]["code"] == "usage"

    invalid = json.loads(ctx.commands["jev"]("{not json"))
    assert invalid["error"]["code"] == "invalid_json"


def test_pre_tool_guard_is_a_local_noop_until_explicitly_enabled():
    ctx = FakeContext()
    plugin.register(ctx)

    assert ctx.hooks["pre_tool_call"](tool_name="terminal", args={"command": "pwd"}) is None


@pytest.mark.parametrize("config, hooks, middleware", [
    ({}, {"pre_tool_call"}, set()),
    ({"skills_mode": "observe"}, {"pre_tool_call", "pre_llm_call"}, set()),
    ({"tools_mode": "observe"}, {"pre_tool_call", "pre_llm_call"}, {"llm_request"}),
    ({"results_mode": "filter"}, {"pre_tool_call", "pre_llm_call", "transform_tool_result"}, set()),
    ({"recovery_mode": "observe"}, {"pre_tool_call", "pre_llm_call", "post_tool_call"}, set()),
    ({"recovery_mode": "suggest"}, {"pre_tool_call", "pre_llm_call", "post_tool_call"}, {"llm_request"}),
])
def test_routing_registration_is_conditional_and_independent_of_guard(config, hooks, middleware):
    ctx = FakeContext(config)
    plugin.register(ctx)
    assert set(ctx.hooks) == hooks
    assert set(ctx.middleware) == middleware
    assert ctx.hooks["pre_tool_call"](tool_name="terminal", args={"command": "pwd"}) is None


def test_older_context_without_middleware_keeps_routing_hooks(caplog):
    ctx = FakeContext({"skills_mode": "observe", "tools_mode": "observe"})
    ctx.register_middleware = None
    plugin.register(ctx)
    assert "pre_llm_call" in ctx.hooks
    assert "middleware_unavailable" in caplog.text


def test_jev_command_delegates_to_evaluate_handler(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            captured.update(kwargs)
            return {"model": "jev-latest", "answers": {"q": {"type": "noul", "noul": 0.5}}}

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    ctx = FakeContext()
    plugin.register(ctx)

    payload = json.dumps(
        {
            "state": "evidence",
            "questions": {"q": {"type": "noul", "instructions": "True?"}},
        }
    )
    result = json.loads(ctx.commands["jev"](payload))
    assert result["ok"] is True
    assert result["answers"]["q"]["noul"] == 0.5
    assert captured["state"] == "evidence"
