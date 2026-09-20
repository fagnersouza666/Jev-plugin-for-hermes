# jev-plugin-for-hermes

Standalone Hermes Agent plugin that exposes TypeSafe Jev as typed decision tools and a namespaced skill.

## Current scope

- `jev_evaluate`: generic System One request with `noul`, `choice`, and `score` questions.
- `jev_price_assess`: convenience adapter for product-price candidates.
- `jev-playbook`: explicit skill, loaded as `jev-plugin-for-hermes:jev-playbook`.
- `/jev`: manual JSON command for inspection and smoke tests.
- No import-time network calls, no credential persistence, and no irreversible action.

The first intended consumer is the existing price-monitor pipeline. The plugin only produces an assessment; numeric policy, alerting, purchase decisions, and human review remain outside the plugin.

## Requirements

- Hermes Agent with the native plugin system.
- Python 3.11+ (the implementation uses only the standard library).
- A TypeSafe API key in `TYPESAFE_API_KEY` when the plugin is enabled.

Agent working notes for this repository live in [`AGENTS.md`](AGENTS.md). The
runtime playbook for calling Jev from Hermes is `skills/jev-playbook/SKILL.md`.

## Development

```bash
cd /home/fagnersouza/Projetos/jev-plugin-for-hermes
python -m pytest -q
```

The test suite is offline and never sends the key or state to the network.

Local caches, virtualenvs, coverage reports, Hermes runtime dirs (`.hermes/`, `plugin-data/`), and secret files (`.env`, `.op.env`, `*.pem`, `*.key`, `auth.json`) are gitignored. Keep `TYPESAFE_API_KEY` out of the repository. Use `.env.example` only for dummy keys.

Validate against the installed Hermes plugin loader:

```bash
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor /home/fagnersouza/Projetos/jev-plugin-for-hermes --ci
```

`hermes plugins doctor` imports the plugin in a temporary Hermes home. It is a loader check, not a Jev API call.

## Local installation (not performed by this project creation)

Keep this project as the canonical source and link it into the active Hermes home:

```bash
mkdir -p "$HOME/.hermes/plugins"
ln -sfn /home/fagnersouza/Projetos/jev-plugin-for-hermes \
  "$HOME/.hermes/plugins/jev-plugin-for-hermes"
hermes plugins enable jev-plugin-for-hermes
```

Then configure the secret through Hermes' normal secret flow, not by committing a `.env` file:

```bash
hermes config set TYPESAFE_API_KEY
```

If the plugin is enabled without a key, Hermes should gate it as missing its declared environment requirement. Do not put a real key in this repository.

## Plugin settings

Settings live under `plugins.entries.jev-plugin-for-hermes.settings` in `config.yaml`:

- `api_url`: default `https://api.typesafe.ai/v1/systemone`.
- `default_model`: default `jev-latest`; pin a tested version for production.
- `timeout_seconds`: default `30.0`, clamped by the client.
- `max_state_chars`: default `20000`, to bound accidental prompt/cost growth.

## Example tool payload

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
      "criteria": ["low", "moderate", "high"]
    }
  }
}
```

The API contract follows TypeSafe's documented `POST /v1/systemone` endpoint. The tool returns the API answers plus usage metadata, wrapped in `{ "ok": true, ... }`; expected local/API failures return `{ "ok": false, "error": ... }` and never expose the response body or API key.

## Security boundary

Hermes plugins run in-process with the user's permissions. This plugin requests no tool override, filesystem, subprocess, browser, or MCP capability. It performs one outbound HTTPS request only when explicitly called. Jev output must not be treated as sole authorization for financial, destructive, or publishing operations.
