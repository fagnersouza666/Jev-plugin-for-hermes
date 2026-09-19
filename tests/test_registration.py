import json
from pathlib import Path

import jev_plugin_for_hermes as plugin


class FakeContext:
    def __init__(self):
        self.tools = {}
        self.skills = {}
        self.commands = {}

    def get_config(self, key, default=None):
        return default

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_skill(self, name, path, description="", **kwargs):
        self.skills[name] = {"path": Path(path), "description": description}

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


def test_registers_tools_skill_and_command():
    ctx = FakeContext()
    plugin.register(ctx)

    assert set(ctx.tools) == {"jev_evaluate", "jev_price_assess"}
    assert ctx.tools["jev_evaluate"]["toolset"] == "jev"
    assert ctx.skills["jev-playbook"]["path"].is_file()
    assert "jev" in ctx.commands

    usage = json.loads(ctx.commands["jev"](""))
    assert usage["error"]["code"] == "usage"
