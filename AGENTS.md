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
| Hook | `pre_tool_call` | Assess every Hermes tool call before execution |
| Skill | `jev-playbook` | Routing and safe-interpretation playbook |
| Command | `/jev <json>` | Manual inspection / smoke test |

At runtime Hermes namespaces tools and the skill as
`jev-plugin-for-hermes:<name>`. Do not hardcode a different plugin id.

## Layout

```
plugin.yaml                 # native manifest (kind: standalone)
__init__.py                 # register(ctx): tools, skill, hook, /jev wiring
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
   only when a registered tool, `/jev`, enabled runtime callback, or external
   operator-invoked cron gate runs.
3. **No privileged capabilities.** Do not declare or use `filesystem`,
   `subprocess`, `browser`, MCP, or tool override. The `pre_tool_call` lifecycle
   hook is intentional and limited to a bounded, sanitized Jev assessment; it
   must not execute tools, authorize irreversible side effects, or expand the
   plugin's capabilities. Each assessed call may make one outbound HTTPS POST.
   Hermes must not read the filesystem. Do not enable `attach_local_files` on
   the Hermes registration path. Codex file excerpts belong in `jev-for-codex`.
4. **Fail closed.** Missing `TYPESAFE_API_KEY`, invalid arguments, HTTP errors,
   timeouts, and malformed responses return structured `{ok: false}` (handlers)
   or typed `JevError` subclasses (client). Never proceed with a partial answer.
5. **Do not leak secrets or raw error bodies.** Authorization is `Bearer` from
   `TYPESAFE_API_KEY`. Handlers must not echo exception text, response bodies,
   or the key. HTTP failures surface as `Jev API returned HTTP <status>`.
6. **Jev is evidence, not authorization.** Tool descriptions, skill text, and
   `jev_price_assess` criteria must keep saying that. Never add a buy / publish /
   send-money / delete path here.
7. **HTTPS except loopback; `api_url` is an operator trust boundary.**
   `_validate_endpoint` rejects credentials, query strings, fragments, and plain
   HTTP except `localhost` / `127.0.0.1` / `::1`. It does **not** block private
   IPs, link-local, or cloud metadata hosts over HTTPS — that is explicit
   operator misconfiguration, not SSRF via tool arguments. Document this in
   README; do not treat a malicious `api_url` as a plugin bug.
8. **Handlers never break the agent loop.** Catch unexpected exceptions in
   `tools.py` and return `internal_error`. Dual import (`from .client` vs
   `from client`) exists so pytest can load the plugin root; keep both paths.
9. **State and wire payloads are bounded.** Default `max_state_chars` is 20000
   (clamped 256–200000). The full POST JSON is capped at 200000 characters;
   at most 32 questions per call. Responses are read with a 1 MiB hard limit.
   Timeout default is 30s (clamped 1–600). The `pre_tool_call` hook caps at 25s.
   Do not remove the clamps.

## Registration contract

Optional advisory routing uses `routing.py` and settings validated through
`PluginSettings.from_ctx`. Modes default to off; observe cannot mutate model
context, schemas, or results. Register `pre_llm_call`, `post_tool_call`,
`transform_tool_result` and `llm_request` only as needed. Errors discard advice
and preserve Hermes behavior; this does not change the guard's fail-closed policy.
Catalogs are operator-supplied settings, never discovered from local files.
See `docs/routing.md` for contracts, limits and rollout.

`cron_gate.py` is a separate operator CLI reading bounded JSON from stdin, never
a registered tool. It cannot execute collectors/jobs. Preserve `_PRICE_QUESTIONS`.
Invalid input never wakes; assessment failures on eligible input wake for review.

`register(ctx)` in `__init__.py` is the only entry point Hermes loads.

- Tools: `ctx.register_tool(name, toolset="jev", schema=..., handler=..., description=...)`.
  The model sees `schema["description"]`. Keep that text in sync with the
  optional `description=` metadata.
- Skill: `ctx.register_skill("jev-playbook", path_to_SKILL.md)`. Do not copy
  skills into `~/.hermes/skills/` (collision risk). Prefer `register_skill`.
- Command: `ctx.register_command("jev", ...)` delegates to `tools.handle_jev` —
  in-session `/jev`, not a `hermes` CLI subcommand. Empty / invalid JSON must
  return usage errors without calling the API.
- Hook: `ctx.register_hook("pre_tool_call", ...)` assesses every Hermes tool
  invocation before execution. Send only a bounded, sanitized preview; return
  no directive only for `allow` + `no_issue`; map `review`, `deny`, provider
  failures, and malformed or contradictory decisions to Hermes' blocking
  directive. Non-allow results include one bounded reason code for diagnostics;
  it never authorizes or bypasses the normal approval path. The hook client uses
  `min(timeout_seconds, 25)` so the outbound call finishes before Hermes'
  default hook callback timeout. Do not enable `attach_local_files` on the Hermes
  registration path.

`plugin.yaml` must stay aligned with code:

- `provides_tools`: `jev_evaluate`, `jev_price_assess`
- `provides_hooks`: `pre_tool_call`, `pre_llm_call`, `post_tool_call`, `transform_tool_result`
- `requires_env`: `TYPESAFE_API_KEY` with `secret: true`
- `config_schema`: client settings, `pre_tool_guard_enabled`, and documented routing settings
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
- `choice`: one named category. `criteria` has 1–255 `{name: description}` alternatives.
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
 line length 120. Dev extras: `pytest>=9.1.1,<10`, `ruff>=0.16.8,<1`,
 `pip-audit>=2.10.1,<3`, and `setuptools>=83.0.0` (PYSEC-2026-3447).
- Tests stay **offline**. Inject `opener` on `JevClient` or patch `JevClient` in
 handlers. Never send `TYPESAFE_API_KEY`, state, or fixtures to the network.
- Package load: `tests/conftest.py` imports `__init__.py` as `jev_plugin_for_hermes`.
- Assert behavior (payload shape, fail-closed codes, no transport when the key
 is missing), not snapshots of incidental strings.
- **pip-audit before every commit.** `scripts/pip-audit.sh` is the same gate as
 GitHub Actions (`python -m pip_audit --skip-editable` after upgrading
 `setuptools>=83.0.0`). Enable it with `bash scripts/install-git-hooks.sh`
 (`core.hooksPath=.githooks`). Do not commit if that script fails.

```bash
python -m pytest -q
python -m ruff check .
bash scripts/pip-audit.sh
TYPESAFE_API_KEY=test-hermes-plugin-key \
 hermes plugins doctor "$PWD" --ci
```

`hermes plugins doctor` is a loader check (temp Hermes home). It is not a Jev
API call. Do not treat a green doctor run as proof the TypeSafe contract still
matches production.

## Local install (source of truth stays this repo)

The user-facing walkthrough (what the plugin does in Hermes, how load/hooks
work, and numbered install steps) lives in [`README.md`](README.md). The
commands below are the operator short form:

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
- Privileged actions (purchase, alerts as side effects, email, filesystem,
  subprocess, browser, MCP). Codex session routing lives in `jev-for-codex`.
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
