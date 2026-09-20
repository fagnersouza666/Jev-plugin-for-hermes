# jev-plugin-for-hermes

Standalone Hermes Agent plugin that exposes TypeSafe Jev (System One) as typed
decision tools and a namespaced skill.

The plugin evaluates supplied evidence and returns `{ "ok": true, ... }` or
`{ "ok": false, "error": ... }`. It does not buy, alert, publish, delete, or
authorize anything.

The first intended consumer is a price-monitor pipeline. Numeric policy, identity
checks, freshness, seller allowlists, alerting, and human review stay **outside**
this plugin.

Contributor working notes live in [`AGENTS.md`](AGENTS.md). The runtime
playbook for calling Jev from Hermes is
[`skills/jev-playbook/SKILL.md`](skills/jev-playbook/SKILL.md)
(`jev-plugin-for-hermes:jev-playbook`).

## Surfaces

| Surface | Name | Purpose |
|---|---|---|
| Tool | `jev_evaluate` | Generic `noul` / `choice` / `score` questions |
| Tool | `jev_price_assess` | Convenience adapter for a product offer |
| Hook | `pre_tool_call` | Assess every Hermes tool call before execution |
| Skill | `jev-playbook` | Routing and safe-interpretation playbook |
| Command | `/jev <json>` | Manual inspection / smoke test |

The `pre_tool_call` hook sends a bounded, sanitized preview of every tool name and
argument object to Jev. `allow` with reason code `no_issue` leaves Hermes' normal
policy and approval path unchanged. `review` and `deny` block the call before
execution; contradictory pairs such as `allow` + `destructive_change` are treated
as malformed decisions and blocked fail-closed. This preserves Hermes' normal
approval and hook ordering, and Jev never gets unilateral authority to execute or
authorize a side effect. If the assessment cannot be completed, the tool call is
blocked fail-closed.

For a non-`allow` result, the hook also requests one bounded reason code (for
example `insufficient_context` or `deployment_or_release`) and includes that
code and its fixed local description in the blocking message. This is diagnostic
context only; it does not turn Jev into an authorization or bypass mechanism.

The Hermes hook does **not** read the filesystem. Codex integration (session
router, PreToolUse adapter, named profiles) lives in a separate project,
[`jev-for-codex`](../jev-for-codex).

This is intentionally an opt-in cost and privacy trade-off: each tool call adds a
synchronous HTTPS round trip and sends the sanitized preview to the configured
TypeSafe endpoint. Common credential fields and inline token patterns are redacted,
but the preview is still derived from tool arguments; do not enable this mode for
sensitive data without accepting that boundary.

At runtime Hermes namespaces tools and the skill as
`jev-plugin-for-hermes:<name>`.

There are no import-time network calls, no credential persistence, and no
irreversible actions. Network calls happen only when a tool or `/jev` runs, or when
the `pre_tool_call` hook assesses a tool invocation.

## Requirements

- Hermes Agent with the native plugin system
- Python 3.11+ (runtime uses the standard library only; no TypeSafe SDK, `httpx`, or `requests`)
- A TypeSafe API key in `TYPESAFE_API_KEY` when the plugin is enabled
  ([TypeSafe console](https://console.typesafe.ai))

## Local installation

Keep this repository as the source of truth and link it into the Hermes home:

```bash
mkdir -p "$HOME/.hermes/plugins"
ln -sfn "$PWD" "$HOME/.hermes/plugins/jev-plugin-for-hermes"
hermes plugins enable jev-plugin-for-hermes
hermes config set TYPESAFE_API_KEY
```

Do not commit `.env`, keys, or a real API URL with credentials. If the plugin is
enabled without a key, Hermes should gate it as missing its declared environment
requirement.

## Plugin settings

Settings live under `plugins.entries.jev-plugin-for-hermes.settings` in Hermes
`config.yaml`. They are resolved once at registration through `PluginSettings`
in `client.py` (not from a repo `.env`):

| Setting | Default | Notes |
|---|---|---|
| `api_url` | `https://api.typesafe.ai/v1/systemone` | Operator trust boundary: Bearer POSTs to this host (HTTPS only, except loopback for local tests). Tool arguments cannot change it. |
| `default_model` | `jev-latest` | Pin a tested version (for example `jev-1.13.0`) after calibration |
| `timeout_seconds` | `30.0` | Clamped once to 1–600 for tools and `/jev`; the `pre_tool_call` hook uses `min(timeout_seconds, 25)` so the Jev call finishes before Hermes' default 30s hook callback timeout |
| `max_state_chars` | `20000` | Clamped once to 256–200000 |

Handlers share one `JevClient.from_settings(...)` instance per registration.
The `/jev` command reuses the evaluate handler via `tools.handle_jev`.

## Question types

Independent questions belong in one `jev_evaluate` call. State must be factual,
compact, JSON-compatible evidence — Jev does not retrieve data.

- **`noul`**: probability that a proposition is true. Requires `instructions`.
- **`choice`**: one named category. `criteria` is a non-empty `{name: description}` object.
- **`score`**: position on an ordered rubric. `criteria` is a list of at least two strings, worst to best.

Question names: `^[A-Za-z][A-Za-z0-9_-]{0,63}$`.

`jev_price_assess` sends a fixed question set (`exact_match`, `condition`,
`seller_risk`, `deal_quality`, `recommendation`). Changing that set changes
downstream price-monitor behavior. Both score questions use worst-to-best
rubrics: a higher `seller_risk` score means a more trustworthy seller, not
more risk.

## Example `jev_evaluate` payload

```json
{
  "state": {
    "target_product": "RTX 5090 32 GB",
    "title": "...",
    "price": 12499,
    "seller": "...",
    "condition": "new"
  },
  "questions": {
    "exact_match": {
      "type": "noul",
      "instructions": "Does this listing exactly match the target product?"
    },
    "risk": {
      "type": "score",
      "instructions": "How risky is this listing?",
      "criteria": ["high", "moderate", "low"]
    }
  }
}
```

The API contract follows TypeSafe's documented `POST /v1/systemone` endpoint.
Success returns allowlisted API fields (`answers`, optional `model` and `usage`)
inside `{ "ok": true, ... }`. The plugin owns `ok` / `error`; vendor fields
cannot overwrite them. Expected local or API failures return
`{ "ok": false, "error": ... }` and never expose the response body or API key.

## Development

```bash
python -m pytest -q
python -m ruff check .
pip install -e '.[dev]' pip-audit==2.10.1 && python -m pip_audit
gitleaks detect --source . --verbose --redact
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor "$PWD" --ci
```

CI runs the pytest, ruff, pip-audit, and gitleaks steps on every push and pull
request (see `.github/workflows/ci.yml`). The test suite is **offline** and never sends the key or state to the network.
`hermes plugins doctor` imports the plugin in a temporary Hermes home. It is a
loader check, not a Jev API call.

Local caches, virtualenvs, coverage reports, Hermes runtime dirs (`.hermes/`,
`plugin-data/`), and secret files (`.env`, `.op.env`, `*.pem`, `*.key`, `*.p12`,
`*.pfx`, `auth.json`, `credentials.json`) are gitignored. Keep
`TYPESAFE_API_KEY` out of the repository. This plugin does not load a repo `.env`;
set the key with `hermes config set TYPESAFE_API_KEY`. A tracked
`.env.example`, if present, must contain dummy values only.

Layout:

```
plugin.yaml                  # native manifest (kind: standalone)
__init__.py                  # register(ctx): tools, skill, /jev wiring
schemas.py                   # model-facing JSON schemas
tools.py                     # handlers + handle_jev (never raise into the agent loop)
client.py                    # PluginSettings, stdlib HTTPS client, request validation
skills/jev-playbook/SKILL.md
tests/                       # offline pytest
docs/development.md          # extra contributor notes
```

## Security boundary

Hermes plugins run in-process with the user's permissions. This plugin requests
no tool override, filesystem, subprocess, browser, or MCP capability. It does
register the documented `pre_tool_call` hook so it can assess calls before they
execute.

- Fail closed on missing `TYPESAFE_API_KEY`, invalid arguments, HTTP errors,
  timeouts, malformed responses, and failed pre-tool assessments.
- The hook sends only a bounded preview; common credential fields and inline token
  patterns are redacted, but no redaction scheme can guarantee that arbitrary user
  content is non-sensitive. Treat the configured TypeSafe endpoint as a recipient
  of tool-argument previews.
- Hermes `pre_tool_call` does not read the local filesystem. `attach_local_files`
  stays off on the registration path. Codex file excerpts belong in `jev-for-codex`.
- `review` and `deny` from Jev block the call before execution. The blocking
  message includes a bounded reason code. The hook never bypasses Hermes' normal
  approval and policy path; Jev is not an authorization system.
- Authorization is `Bearer` from `TYPESAFE_API_KEY`. Handlers must not echo
  exception text, response bodies, or the key.
- **`api_url` is an operator trust boundary.** The plugin sends `Authorization:
  Bearer` to whatever host Hermes configures. `_validate_endpoint` enforces HTTPS
  (except loopback for local tests) and rejects credentials, query strings, and
  fragments, but it does not block private IPs, link-local, or cloud metadata
  URLs. That is explicit operator configuration, not SSRF via tool arguments.
  Point `api_url` only at a trusted TypeSafe endpoint (or a local mock).
- The client does not follow redirects, so the Bearer token stays on the
  validated `api_url`.
- Request bounds: serialized state up to `max_state_chars`, full JSON payload
  up to 200000 characters, at most 32 questions, strict question allowlist, no
  `NaN` / `Infinity` in JSON.
- Response bounds: at most 1 MiB read before parsing; incomplete or mistyped
  `answers` objects are rejected.

## License

MIT
