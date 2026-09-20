import json
import re
from pathlib import Path

import jev_plugin_for_hermes as plugin
from jev_plugin_for_hermes import schemas
from jev_plugin_for_hermes.client import PLUGIN_VERSION


class FakeContext:
    def __init__(self):
        self.tools = {}
        self.skills = {}
        self.commands = {}
        self.hooks = {}

    def get_config(self, key, default=None):
        return default

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def register_skill(self, name, path, description="", **kwargs):
        self.skills[name] = {"path": Path(path), "description": description}

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_hook(self, name, callback):
        self.hooks[name] = callback


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
