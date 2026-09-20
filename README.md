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
| Skill | `jev-playbook` | Routing and safe-interpretation playbook |
| Command | `/jev <json>` | Manual inspection / smoke test |

At runtime Hermes namespaces tools and the skill as
`jev-plugin-for-hermes:<name>`.

There are no import-time network calls, no credential persistence, and no
irreversible actions. The only side effect is one outbound HTTPS POST when a
tool or `/jev` runs.

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
| `api_url` | `https://api.typesafe.ai/v1/systemone` | Operator trust boundary: one Bearer POST to this host (HTTPS only, except loopback for local tests). Tool arguments cannot change it. |
| `default_model` | `jev-latest` | Pin a tested version (for example `jev-1.13.0`) after calibration |
| `timeout_seconds` | `30.0` | Clamped once to 1–600 |
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
`TYPESAFE_API_KEY` out of the repository. This plugin does not load `.env`;
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
no tool override, filesystem, subprocess, browser, or MCP capability.

- Fail closed on missing `TYPESAFE_API_KEY`, invalid arguments, HTTP errors,
  timeouts, and malformed responses.
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
- Jev output is evidence, not authorization. Do not treat confidence as a
  purchase, publish, delete, or send-money decision.

## License

MIT
