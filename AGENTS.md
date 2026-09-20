# AGENTS.md — jev-plugin-for-hermes

Instructions for agents and developers working **on this plugin**. For how an
agent should *use* Jev at runtime, load `skills/jev-playbook/SKILL.md`
(`jev-plugin-for-hermes:jev-playbook`).

This is a standalone Hermes native plugin (`plugin.yaml` + `register(ctx)`).
It is not part of `NousResearch/hermes-agent` and must not be merged into that
tree. Third-party product plugins stay out-of-tree by Hermes policy.

## What this plugin is

Typed decision primitives over TypeSafe Jev (System One). The plugin evaluates
supplied evidence and returns `{ "ok": true, ... }` or `{ "ok": false, "error": ... }`.
It does not buy, alert, publish, delete, or authorize anything.

First intended consumer: a price-monitor pipeline. Numeric policy, identity
checks, freshness, seller allowlists, alerting, and human review stay **outside**.

Surfaces:

| Surface | Name | Purpose |
|---|---|---|
| Tool | `jev_evaluate` | Generic `noul` / `choice` / `score` questions |
| Tool | `jev_price_assess` | Convenience adapter for a product offer |
| Skill | `jev-playbook` | Routing and safe-interpretation playbook |
| Command | `/jev <json>` | Manual inspection / smoke test |

At runtime Hermes namespaces tools and the skill as
`jev-plugin-for-hermes:<name>`. Do not hardcode a different plugin id.

## Layout

```
plugin.yaml                 # native manifest (kind: standalone)
__init__.py                 # register(ctx): tools, skill, /jev wiring
schemas.py                  # model-facing JSON schemas
tools.py                    # handlers + handle_jev (never raise into the agent loop)
client.py                   # PluginSettings, stdlib HTTPS client, request validation
skills/jev-playbook/SKILL.md
tests/                      # offline pytest; package loaded as jev_plugin_for_hermes
```

Keep the hyphenated directory name. Tests alias the root as
`jev_plugin_for_hermes` in `tests/conftest.py` because the folder name is not a
valid Python identifier.

## Invariants (do not break)

1. **Stdlib only.** No runtime dependencies. Do not add the TypeSafe SDK, `httpx`,
   `requests`, or a vendor client. The HTTP contract must stay explicit in
   `client.py`.
2. **No import-time network, filesystem writes, or secret I/O.** API calls happen
   only when a registered tool or `/jev` runs.
3. **No privileged capabilities.** Do not declare or use `filesystem`,
   `subprocess`, `browser`, MCP, tool override, or lifecycle hooks unless a
   future change has an explicit, reviewed reason. This plugin's only side
   effect is one outbound HTTPS POST.
4. **Fail closed.** Missing `TYPESAFE_API_KEY`, invalid arguments, HTTP errors,
   timeouts, and malformed responses return structured `{ok: false}` (handlers)
   or typed `JevError` subclasses (client). Never proceed with a partial answer.
5. **Do not leak secrets or raw error bodies.** Authorization is `Bearer` from
   `TYPESAFE_API_KEY`. Handlers must not echo exception text, response bodies,
   or the key. HTTP failures surface as `Jev API returned HTTP <status>`.
6. **Jev is evidence, not authorization.** Tool descriptions, skill text, and
   `jev_price_assess` criteria must keep saying that. Never add a buy / publish /
   send-money / delete path here.
7. **HTTPS except loopback.** `_validate_endpoint` rejects credentials, query
   strings, fragments, and plain HTTP except `localhost` / `127.0.0.1` / `::1`.
8. **Handlers never break the agent loop.** Catch unexpected exceptions in
   `tools.py` and return `internal_error`. Dual import (`from .client` vs
   `from client`) exists so pytest can load the plugin root; keep both paths.
9. **State and wire payloads are bounded.** Default `max_state_chars` is 20000
   (clamped 256–200000). The full POST JSON is capped at 200000 characters;
   at most 32 questions per call. Responses are read with a 1 MiB hard limit.
   Timeout default is 30s (clamped 1–600). Do not remove the clamps.

## Registration contract

`register(ctx)` in `__init__.py` is the only entry point Hermes loads.

- Tools: `ctx.register_tool(name, toolset="jev", schema=..., handler=..., description=...)`.
  The model sees `schema["description"]`. Keep that text in sync with the
  optional `description=` metadata.
- Skill: `ctx.register_skill("jev-playbook", path_to_SKILL.md)`. Do not copy
  skills into `~/.hermes/skills/` (collision risk). Prefer `register_skill`.
- Command: `ctx.register_command("jev", ...)` delegates to `tools.handle_jev` —
  in-session `/jev`, not a `hermes` CLI subcommand. Empty / invalid JSON must
  return usage errors without calling the API.

`plugin.yaml` must stay aligned with code:

- `provides_tools`: `jev_evaluate`, `jev_price_assess`
- `requires_env`: `TYPESAFE_API_KEY` with `secret: true`
- `config_schema`: `api_url`, `default_model`, `timeout_seconds`, `max_state_chars`
- `kind: standalone`, `manifest_version: 2`

Settings are read via `PluginSettings.from_ctx(ctx)` in `client.py` (defaults
and clamps applied once), not from `.env` in this repo. `make_handlers` builds
one `JevClient.from_settings(...)` per registration. Users set the key with
`hermes config set TYPESAFE_API_KEY`.

## Changing a tool

1. Update the JSON schema in `schemas.py` (this is what the model reads).
2. Update validation and handler behavior in `client.py` / `tools.py`.
3. If the name is new or removed, update `plugin.yaml` `provides_tools` and
   `register()`.
4. Add or adjust tests in `tests/test_tools.py` and `tests/test_client.py`.
5. If the decision semantics change, update `skills/jev-playbook/SKILL.md` and
   `README.md` in the same change.

Question types:

- `noul`: probability a proposition is true. Needs `instructions`.
- `choice`: one named category. `criteria` is a non-empty `{name: description}` object.
- `score`: position on an ordered rubric. `criteria` is a list of ≥ 2 strings,
  worst to best.

Question names: `^[A-Za-z][A-Za-z0-9_-]{0,63}$`. Independent questions belong
in one `jev_evaluate` call. State must be factual, compact, JSON-compatible
evidence — Jev does not retrieve data.

`jev_price_assess` builds a fixed question set in `_PRICE_QUESTIONS`. Treat that
set as product policy: changing it changes downstream price-monitor behavior.
`seller_risk` and `deal_quality` both use worst-to-best score rubrics (higher
score = better purchase signal).

## Python and tests

- Python ≥ 3.11, `from __future__ import annotations`, ruff `target-version = py311`,
  line length 120. Dev extras: `pytest>=8,<10` and `ruff>=0.9,<1`.
- Tests stay **offline**. Inject `opener` on `JevClient` or patch `JevClient` in
  handlers. Never send `TYPESAFE_API_KEY`, state, or fixtures to the network.
- Package load: `tests/conftest.py` imports `__init__.py` as `jev_plugin_for_hermes`.
- Assert behavior (payload shape, fail-closed codes, no transport when the key
  is missing), not snapshots of incidental strings.

```bash
python -m pytest -q
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor "$PWD" --ci
```

`hermes plugins doctor` is a loader check (temp Hermes home). It is not a Jev
API call. Do not treat a green doctor run as proof the TypeSafe contract still
matches production.

## Local install (source of truth stays this repo)

```bash
mkdir -p "$HOME/.hermes/plugins"
ln -sfn "$PWD" "$HOME/.hermes/plugins/jev-plugin-for-hermes"
hermes plugins enable jev-plugin-for-hermes
hermes config set TYPESAFE_API_KEY
```

Do not commit `.env`, keys, or a real API URL with credentials. Pin
`default_model` (e.g. `jev-1.13.0`) only after fixtures and thresholds are
calibrated; `jev-latest` is for exploration.

## Out of scope

- Hermes core patches (`run_agent.py`, `cli.py`, gateway, `hermes_cli/main.py`).
- Irreversible or privileged actions (purchase, alerts as side effects, email,
  filesystem, subprocess).
- Importing a vendor SDK “to make the client simpler”.
- Portable Agent Plugins v1 (`plugin.json` + `mcp.json`) as a replacement for
  this native plugin.
- Treating Jev confidence as a policy decision.

When adding a feature, prefer extending `jev_evaluate` questions or a thin
adapter like `jev_price_assess` over new privileged surface.

## Documentation language

- **`README.md` is English only.** Do not add Portuguese or any other language
  to it. User-facing install, usage, settings, and security notes belong there.
- `AGENTS.md` and `skills/jev-playbook/SKILL.md` stay English.
- Extra contributor notes go in `docs/` and should also be English so the
  public tree has one language. Do not commit Portuguese review dumps into
  `README.md`.
