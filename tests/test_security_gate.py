"""Keep the GitHub pip-audit gate and the local pre-commit hook on the same command."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIP_AUDIT_SCRIPT = ROOT / "scripts" / "pip-audit.sh"
PRE_COMMIT_HOOK = ROOT / ".githooks" / "pre-commit"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"
SETUPTOOLS_FLOOR = "setuptools>=83.0.0"


def test_dev_extras_require_patched_setuptools_and_pip_audit():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]["dev"]
    assert any(item.startswith(SETUPTOOLS_FLOOR) for item in extras)
    assert any(item.startswith("pip-audit") for item in extras)
    build_requires = data["build-system"]["requires"]
    assert any(item.startswith(SETUPTOOLS_FLOOR) for item in build_requires)


def test_pip_audit_script_upgrades_setuptools_and_skips_editable():
    script = PIP_AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert SETUPTOOLS_FLOOR in script
    assert "--skip-editable" in script
    assert "python -m pip_audit" in script


def test_ci_security_job_runs_shared_pip_audit_script():
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/pip-audit.sh" in ci
    assert SETUPTOOLS_FLOOR in ci


def test_pre_commit_hook_runs_shared_pip_audit_script():
    hook = PRE_COMMIT_HOOK.read_text(encoding="utf-8")
    assert "scripts/pip-audit.sh" in hook
    assert PIP_AUDIT_SCRIPT.is_file()
    assert PRE_COMMIT_HOOK.stat().st_mode & 0o111
    assert PIP_AUDIT_SCRIPT.stat().st_mode & 0o111


def test_install_git_hooks_sets_local_hooks_path():
    script = (ROOT / "scripts" / "install-git-hooks.sh").read_text(encoding="utf-8")
    assert "git config --local core.hooksPath .githooks" in script
