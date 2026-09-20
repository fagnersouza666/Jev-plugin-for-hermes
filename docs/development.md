# Development notes

Companion to the root [`README.md`](../README.md) (English only) and
[`AGENTS.md`](../AGENTS.md). This file records contributor conventions that
should stay in the repository.

## Tests

```bash
python -m pytest -q
python -m ruff check .
pip install -e '.[dev]' pip-audit==2.10.1 && python -m pip_audit
gitleaks detect --source . --verbose --redact
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor "$PWD" --ci
```

GitHub Actions (`.github/workflows/ci.yml`) runs pytest, ruff, pip-audit, and
gitleaks on every push and pull request.

- Tests stay offline: inject `opener` on `JevClient` or patch `JevClient` in
  handlers (fake classes need a `from_settings` classmethod because
  `make_handlers` builds one client per registration). Never send
  `TYPESAFE_API_KEY`, state, or fixtures to the network.
- Guard tests in `tests/test_tools.py` cover reason codes and the Hermes default
  (no filesystem reads). Codex PreToolUse and session routing live in
  `jev-for-codex`.
- `FakeResponse.read` should accept an optional `n` because production reads
  responses with a byte limit. The default client opener disables HTTP redirects.
- `tests/conftest.py` loads the plugin root as `jev_plugin_for_hermes` because
  the hyphenated directory name is not a valid Python identifier.
- `hermes plugins doctor` is a loader check in a temporary Hermes home, not a
  TypeSafe API call.

## Gitignore (plugin checkout)

Ignore local caches, virtualenvs, coverage, Hermes runtime dirs (`.hermes/`,
`plugin-data/`), and secrets:

- `.env`, `.env.*` except `.env.example`
- `.op.env`
- `*.pem`, `*.key`, `*.p12`, `*.pfx`
- `auth.json`, `credentials.json`

Keep `TYPESAFE_API_KEY` out of the repository. The Hermes plugin does not load
a repo `.env`. A tracked `.env.example`, if present, may contain dummy keys only.

## Documentation

`README.md` must remain English. Persist project knowledge here or in
`AGENTS.md` instead of mixing languages in the root README.

Full-tree defect reports live in [`bug-report.md`](bug-report.md) (full scan
20/09/2026; historical BUG-001–011 stay in the appendix as resolved).

Security audits (OWASP/LGPD, threat model) live in
[`relatorio-seguranca.md`](relatorio-seguranca.md).

## `api_url` trust boundary

`api_url` is operator-controlled Hermes config, not a tool argument. The
plugin sends one Bearer POST to the configured host. `_validate_endpoint` enforces
HTTPS (except loopback for local tests) but does not block private IPs or cloud
metadata URLs. Misconfigured `api_url` exposes `TYPESAFE_API_KEY` to the chosen
host — document this in README/AGENTS; do not add RFC1918 blocking unless a
future change explicitly requires it.
