# Development notes

Companion to the root [`README.md`](../README.md) (English only) and
[`AGENTS.md`](../AGENTS.md). This file records contributor conventions that
should stay in the repository.

## Tests

```bash
python -m pytest -q
TYPESAFE_API_KEY=test-hermes-plugin-key \
  hermes plugins doctor "$PWD" --ci
```

- Tests stay offline: inject `opener` on `JevClient` or patch `JevClient` in
  handlers (fake classes need a `from_settings` classmethod because
  `make_handlers` builds one client per registration). Never send
  `TYPESAFE_API_KEY`, state, or fixtures to the network.
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

Keep `TYPESAFE_API_KEY` out of the repository. The plugin does not load `.env`.
A tracked `.env.example`, if present, may contain dummy keys only.

## Documentation

`README.md` must remain English. Persist project knowledge here or in
`AGENTS.md` instead of mixing languages in the root README.
