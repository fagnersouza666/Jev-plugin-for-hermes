#!/usr/bin/env bash
# Point this clone at versioned hooks so pip-audit runs before every commit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

chmod +x .githooks/pre-commit scripts/pip-audit.sh scripts/install-git-hooks.sh
git config --local core.hooksPath .githooks
echo "Configured core.hooksPath=.githooks (pip-audit runs before every commit)."
