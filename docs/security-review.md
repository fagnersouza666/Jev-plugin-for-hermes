# Security review — pip-audit gate and pre-commit hook

> System: jev-plugin-for-hermes
> Date: 2026-09-20 | Version: 0.2.2
> Stack: Python 3.11+ / Hermes Agent plugin
> Based on: OWASP Top 10:2021, LGPD (Lei 13.709/2018), CWE
> Mode: **quick** (files created or modified in this change)

## Security summary

- CRITICAL: 0
- HIGH: 0
- MEDIUM/LOW: 0
- Positives: CI still uses `permissions: contents: read`; both jobs upgrade `setuptools>=83.0.0` (PYSEC-2026-3447 / CVE-2026-59890) before install; `scripts/pip-audit.sh` is the same fail-closed gate as GitHub Actions and `.githooks/pre-commit`; `install-git-hooks.sh` sets `core.hooksPath` with `--local` only; gitleaks still installed with `curl -fSL` and a pinned release; no secrets added; runtime `dependencies` remain empty.
- Verdict: **PASS**

Scope: `pyproject.toml`, `.github/workflows/ci.yml`, `scripts/pip-audit.sh`, `scripts/install-git-hooks.sh`, `.githooks/pre-commit`, `tests/test_security_gate.py`, `README.md`, `AGENTS.md`, `docs/development.md`, `docs/relatorio-seguranca.md`.

No secrets were introduced. README remains English-only. `pip-audit` stays a networked gate outside the offline pytest suite.
