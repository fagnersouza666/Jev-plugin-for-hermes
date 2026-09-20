# jev-plugin-for-hermes

Standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin
that exposes [TypeSafe Jev](https://console.typesafe.ai) (System One) as typed
decision tools, a namespaced skill, a `/jev` command, and a `pre_tool_call`
guard.

The plugin evaluates evidence you already have and returns
`{ "ok": true, ... }` or `{ "ok": false, "error": ... }`. It does not buy,
alert, publish, delete, or authorize anything.

Contributor notes: [`AGENTS.md`](AGENTS.md). Runtime playbook for the agent:
[`skills/jev-playbook/SKILL.md`](skills/jev-playbook/SKILL.md)
(`jev-plugin-for-hermes:jev-playbook`).

## What it is for

Use this plugin when a Hermes session needs a **typed judgment over supplied
evidence**, not when it needs to fetch data or take a side effect.

Typical jobs:

- Classify or score a product listing (exact match, condition, seller quality,
  deal quality).
- Ask a yes/no-style probability (`noul`), pick one named category (`choice`),
  or place an item on an ordered rubric (`score`).
- Smoke-test a Jev payload from the session with `/jev`.
- Once the plugin is enabled, assess **every** Hermes tool call before it
  runs, so a `review` / `deny` from Jev can block the call. That assessment
  is evidence for Hermes' normal policy, not a replacement for it.

The first intended consumer is a price-monitor pipeline. Numeric thresholds,
identity checks, freshness, seller allowlists, alerting, and human review stay
**outside** this plugin. Treat Jev output as input to those checks.

This repo is a third-party native plugin (`plugin.yaml` + `register(ctx)`). It
is not part of `NousResearch/hermes-agent` and must not be merged into that
tree.

## How it works in Hermes Agent

Hermes discovers user plugins under `~/.hermes/plugins/`. A plugin is **opt-in**:
discovery can see the directory, but tools and hooks load only after the name
is on `plugins.enabled` in `~/.hermes/config.yaml`.

On load, Hermes reads `plugin.yaml` and calls `register(ctx)` in `__init__.py`.
That function registers these surfaces:

| Surface | Name | What the agent sees |
|---|---|---|
| Tool | `jev_evaluate` | Generic `noul` / `choice` / `score` questions |
| Tool | `jev_price_assess` | Fixed question set for a product offer |
| Hook | `pre_tool_call` | Assesses every Hermes tool call before execution |
| Skill | `jev-playbook` | Routing and safe-interpretation playbook |
| Command | `/jev <json>` | In-session inspection / smoke test |

At runtime Hermes namespaces tools and the skill as
`jev-plugin-for-hermes:<name>`. Do not hardcode a different plugin id.

### Session flow

1. You start Hermes (CLI, gateway, or desktop). The plugin manager imports this
   directory only if `jev-plugin-for-hermes` is enabled and
   `TYPESAFE_API_KEY` is present.
2. `register(ctx)` builds one `JevClient` from plugin settings and wires the
   two tools, the skill, `/jev`, and the hook.
3. When the model calls `jev-plugin-for-hermes:jev_evaluate` or
   `jev-plugin-for-hermes:jev_price_assess`, the handler validates the
   arguments, POSTs JSON to the configured TypeSafe endpoint over HTTPS, and
   returns allowlisted fields inside `{ "ok": true, ... }`.
4. Before **any** Hermes tool runs (not only Jev tools), `pre_tool_call` sends
   a bounded, sanitized preview of the tool name and arguments to Jev.
   `allow` with reason code `no_issue` leaves Hermes' normal approval path
   unchanged. `review`, `deny`, provider failures, and contradictory decisions
   (for example `allow` + `destructive_change`) block the call fail-closed.
5. `/jev` reuses the evaluate handler. Empty or invalid JSON returns a usage
   error and does not call the API.

There are no import-time network calls, no credential persistence in this
repo, and no irreversible actions. Network happens only when a tool, `/jev`,
or the hook runs. Each of those is one outbound HTTPS POST.

The hook is an opt-in cost and privacy trade-off: every tool call adds a
synchronous round trip and sends a redacted preview to TypeSafe. Common
credential fields and inline token patterns are stripped, but the preview is
still derived from tool arguments.

Jev never gets unilateral authority to execute a side effect. A non-`allow`
result includes one bounded reason code (for example `insufficient_context`)
for diagnostics only.

The Hermes hook does **not** read the filesystem. Codex session routing lives
in a separate project, [`jev-for-codex`](../jev-for-codex).

## Requirements

- Hermes Agent with the native plugin system (`hermes` on your `PATH`)
- Python 3.11+ (the plugin uses the standard library only: no TypeSafe SDK,
  `httpx`, or `requests`)
- A TypeSafe API key in `TYPESAFE_API_KEY`
  ([TypeSafe console](https://console.typesafe.ai))

## Install in Hermes (step by step)

Keep this repository as the source of truth. Linking it into the Hermes home
means edits here are what the agent loads. Do not copy files into
`~/.hermes/skills/` (collision risk); the skill is registered from this tree.

### 1. Confirm Hermes is installed

```bash
hermes --version
```

If that fails, install Hermes Agent first from
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

### 2. Get a TypeSafe API key

1. Open [https://console.typesafe.ai](https://console.typesafe.ai).
2. Create or copy an API key.
3. Keep it out of git, prompts, and this repository.

### 3. Get this plugin onto disk

Clone (or use an existing checkout):

```bash
git clone https://github.com/fagnersouza666/Jev-plugin-for-hermes.git
cd Jev-plugin-for-hermes
```

The directory name on GitHub can differ from the plugin id. The id Hermes
uses is the `name` in `plugin.yaml`: `jev-plugin-for-hermes`.

### 4. Link the checkout into the Hermes plugin directory

```bash
mkdir -p "$HOME/.hermes/plugins"
ln -sfn "$PWD" "$HOME/.hermes/plugins/jev-plugin-for-hermes"
```

`$PWD` must be the plugin root (the folder that contains `plugin.yaml` and
`__init__.py`). The symlink name must be `jev-plugin-for-hermes`.

Alternative if you prefer Hermes to clone the repo itself:

```bash
hermes plugins install https://github.com/fagnersouza666/Jev-plugin-for-hermes.git --enable
```

That path is a copy under `~/.hermes/plugins/`, not a live link to this
working tree. Use the symlink when you are developing the plugin.

This plugin is not in the official Hermes catalog, so a bare
`hermes plugins install jev-plugin-for-hermes` will not resolve it.

### 5. Enable the plugin

User plugins stay disabled until you allow them:

```bash
hermes plugins enable jev-plugin-for-hermes
```

You can also toggle plugins interactively with `hermes plugins`, or add the
id under `plugins.enabled` in `~/.hermes/config.yaml`.

Enabling the plugin also registers `pre_tool_call`. After that, every Hermes
tool invocation is assessed (one HTTPS POST per call) until you disable the
plugin.

If the plugin is enabled without a key, Hermes should gate it as missing its
declared environment requirement.

### 6. Store the API key in Hermes

```bash
hermes config set TYPESAFE_API_KEY
```

Enter the key when prompted. `UPPER_SNAKE` names go to `~/.hermes/.env`, not
`config.yaml`. Do not put the key in this repo, in `plugin.yaml`, or in a
prompt.

### 7. Optional: pin settings

Settings live under `plugins.entries.jev-plugin-for-hermes.settings` in
`~/.hermes/config.yaml`. They are resolved once at registration through
`PluginSettings` in `client.py` (not from a repo `.env`).

Example:

```yaml
plugins:
  enabled:
    - jev-plugin-for-hermes
  entries:
    jev-plugin-for-hermes:
      settings:
        api_url: https://api.typesafe.ai/v1/systemone
        default_model: jev-latest
        timeout_seconds: 30.0
        max_state_chars: 20000
```

| Setting | Default | Notes |
|---|---|---|
| `api_url` | `https://api.typesafe.ai/v1/systemone` | Operator trust boundary: Bearer POSTs go to this host (HTTPS only, except loopback for local tests). Tool arguments cannot change it. |
| `default_model` | `jev-latest` | Pin a tested version (for example `jev-1.13.0`) after calibration |
| `timeout_seconds` | `30.0` | Clamped once to 1–600 for tools and `/jev`; the `pre_tool_call` hook uses `min(timeout_seconds, 25)` so the Jev call finishes before Hermes' default 30s hook callback timeout |
| `max_state_chars` | `20000` | Clamped once to 256–200000 |

Handlers share one `JevClient.from_settings(...)` instance per registration.

### 8. Verify the loader (no TypeSafe call)

```bash
hermes plugins list --user
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor "$HOME/.hermes/plugins/jev-plugin-for-hermes" --ci
```

`hermes plugins doctor` imports the plugin in a temporary Hermes home. It is a
loader check, not a Jev API call. A green doctor run does not prove the
production TypeSafe contract.

If the plugin does not appear, run:

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

### 9. Start a new Hermes session and try it

Restart the CLI session (and the gateway, if you use one) so the enabled
plugin is imported.

In the session you should see the plugin in `/plugins`, and the model can
call `jev-plugin-for-hermes:jev_evaluate` and
`jev-plugin-for-hermes:jev_price_assess`. Load the playbook with the
namespaced skill `jev-plugin-for-hermes:jev-playbook`.

Smoke-test without waiting for the model:

```text
/jev {"state":{"title":"RTX 5090 32 GB","price":12499},"questions":{"exact_match":{"type":"noul","instructions":"Does this listing exactly match RTX 5090 32 GB?"}}}
```

A successful call returns `{ "ok": true, ... }` with `answers`. A missing key,
timeout, HTTP error, or malformed body returns `{ "ok": false, "error": ... }`
and never echoes the API key or the raw response body.

### Disable or uninstall

```bash
hermes plugins disable jev-plugin-for-hermes
```

That leaves the files in place. To drop a symlink install:

```bash
rm "$HOME/.hermes/plugins/jev-plugin-for-hermes"
```

That removes the link, not this repository. If you installed from git with
`hermes plugins install`, use `hermes plugins remove <name>` after checking
`hermes plugins list`.

## Question types

Independent questions belong in one `jev_evaluate` call. State must be factual,
compact, JSON-compatible evidence. Jev does not retrieve data.

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
cannot overwrite them.

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
request (see `.github/workflows/ci.yml`). The test suite is **offline** and
never sends the key or state to the network.

Local caches, virtualenvs, coverage reports, Hermes runtime dirs (`.hermes/`,
`plugin-data/`), and secret files (`.env`, `.op.env`, `*.pem`, `*.key`, `*.p12`,
`*.pfx`, `auth.json`, `credentials.json`) are gitignored. Keep
`TYPESAFE_API_KEY` out of the repository.

Layout:

```
plugin.yaml                  # native manifest (kind: standalone)
__init__.py                  # register(ctx): tools, skill, hook, /jev wiring
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
