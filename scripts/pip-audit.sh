#!/usr/bin/env bash
# Same pip-audit gate as GitHub Actions job "security".
# Run locally before every commit (see .githooks/pre-commit).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .venv/bin/activate ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip "setuptools>=83.0.0"
python -m pip install -e '.[dev]'
exec python -m pip_audit --skip-editable
