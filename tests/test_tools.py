import json

from jev_plugin_for_hermes.tools import (
    _PRICE_QUESTIONS,
    _TOOL_CALL_QUESTIONS,
    _TOOL_CALL_REASON_CODES,
    _sanitize_tool_value,
    _tool_call_state,
    make_handlers,
    make_tool_call_guard,
    normalize_hook_cwd,
)


def test_price_assess_returns_configuration_error_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    _evaluate, assess = make_handlers({})
    result = json.loads(
        assess(
            {
                "target": "RTX 5090 32 GB",
                "offer": {"title": "RTX 5090", "price": 12000},
            }
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "configuration"
    assert "TYPESAFE_API_KEY" in result["error"]["message"]


def test_handlers_reject_bad_shapes():
    evaluate, assess = make_handlers({})
    assert json.loads(evaluate([]))["error"]["code"] == "invalid_arguments"
    assert json.loads(assess({"target": "x", "offer": []}))["error"]["code"] == "invalid_arguments"


def test_evaluate_uses_injected_client(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            assert kwargs["state"] == "hello"
            assert kwargs["model"] == "jev-1.13.0"
            return {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.8}}}

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    evaluate, _assess = make_handlers({"default_model": "jev-1.13.0"})
    result = json.loads(
        evaluate(
            {
                "state": "hello",
                "questions": {"q": {"type": "noul", "instructions": "Is this true?"}},
            }
        )
    )
    assert result["ok"] is True
    assert result["answers"]["q"]["noul"] == 0.8


def test_price_assess_success_with_injected_client(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            assert kwargs["state"]["target_product"] == "RTX 5090"
            assert "exact_match" in kwargs["questions"]
            return {"model": "jev-latest", "answers": {"exact_match": {"type": "noul", "noul": 0.7}}}

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    _evaluate, assess = make_handlers({})
    result = json.loads(
        assess(
            {
                "target": "RTX 5090",
                "offer": {"title": "RTX 5090", "price": 12000},
            }
        )
    )
    assert result["ok"] is True
    assert result["assessment"]["answers"]["exact_match"]["noul"] == 0.7


def test_evaluate_internal_error_does_not_leak_exception_text(monkeypatch):
    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            raise RuntimeError("secret-token-leak")

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", BrokenClient)
    evaluate, _assess = make_handlers({})
    result = json.loads(
        evaluate(
            {
                "state": "hello",
                "questions": {"q": {"type": "noul", "instructions": "Is this true?"}},
            }
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "internal_error"
    assert "secret-token-leak" not in result["error"]["message"]


def test_price_questions_seller_risk_is_worst_to_best():
    seller_risk = _PRICE_QUESTIONS["seller_risk"]
    assert seller_risk["instructions"] == (
        "How trustworthy is the seller or listing for a purchase decision?"
    )
    assert seller_risk["criteria"] == [
        "High risk signals, weak evidence, or suspicious seller",
        "Some uncertainty or moderate risk signals",
        "No meaningful risk signals and strong evidence",
    ]


def test_evaluate_preserves_plugin_ok_envelope(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            return {
                "ok": False,
                "error": {"code": "vendor", "message": "should not overwrite"},
                "answers": {"q": {"type": "noul", "noul": 0.8}},
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 1},
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    evaluate, _assess = make_handlers({})
    result = json.loads(
        evaluate(
            {
                "state": "hello",
                "questions": {"q": {"type": "noul", "instructions": "Is this true?"}},
            }
        )
    )
    assert result["ok"] is True
    assert "error" not in result
    assert result["answers"]["q"]["noul"] == 0.8
    assert result["model"] == "jev-1.13.0"
    assert result["usage"] == {"input_tokens": 1}


def test_price_assess_nests_allowlisted_payload_without_nested_ok(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            return {
                "ok": False,
                "answers": {"exact_match": {"type": "noul", "noul": 0.7}},
                "model": "jev-latest",
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    _evaluate, assess = make_handlers({})
    result = json.loads(
        assess(
            {
                "target": "RTX 5090",
                "offer": {"title": "RTX 5090", "price": 12000},
            }
        )
    )
    assert result["ok"] is True
    assert result["assessment"] == {
        "answers": {"exact_match": {"type": "noul", "noul": 0.7}},
        "model": "jev-latest",
    }
    assert "ok" not in result["assessment"]


def test_json_output_rejects_nan_values(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            return {
                "answers": {"q": {"type": "noul", "noul": float("nan")}},
                "model": "jev-latest",
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    evaluate, _assess = make_handlers({})
    result = json.loads(
        evaluate(
            {
                "state": "hello",
                "questions": {"q": {"type": "noul", "instructions": "Is this true?"}},
            }
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "internal_error"
    assert "nan" not in result["error"]["message"].lower()


def test_price_assess_internal_error_does_not_leak_exception_text(monkeypatch):
    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls(**kwargs)

        def evaluate(self, **kwargs):
            raise ValueError("internal-detail")

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", BrokenClient)
    _evaluate, assess = make_handlers({})
    result = json.loads(
        assess(
            {
                "target": "Widget",
                "offer": {"title": "Widget", "price": 10},
            }
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "internal_error"
    assert "internal-detail" not in result["error"]["message"]


def test_tool_call_preview_redacts_sensitive_values_and_bounds_text():
    preview = _sanitize_tool_value(
        {
            "command": "curl -H 'Authorization: " + "Bearer " + "abc123xyz' https://example.test",
            "password": "do-not-send",
            "content": "x" * 600,
        }
    )
    assert preview["password"] == "[REDACTED]"
    assert "[REDACTED]" in preview["command"]
    assert "truncated" in preview["content"]


def test_tool_call_state_caps_large_previews():
    state = _tool_call_state("write_file", {f"field_{i}": "x" * 512 for i in range(64)})
    assert state["arguments"]["[preview_truncated]"] is True
    assert state["arguments"]["original_preview_chars"] > 12_000


def test_tool_call_guard_allows_a_clear_call(monkeypatch):
    captured = {}

    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            captured.update(kwargs)
            assert set(kwargs["questions"]) == {"action", "reason_code"}
            return {
                "answers": {
                    "action": {"type": "choice", "choice": "allow"},
                    "reason_code": {"type": "choice", "choice": "no_issue"},
                }
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    guard = make_tool_call_guard({})
    assert guard(tool_name="read_file", args={"path": "/tmp/example.txt"}) is None
    assert captured["state"]["tool_name"] == "read_file"
    assert captured["state"]["arguments"]["path"] == "/tmp/example.txt"


def test_make_tool_call_guard_uses_tighter_timeout_than_default_settings(monkeypatch):
    captured = {}

    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            captured["timeout_seconds"] = settings.timeout_seconds
            return cls()

        def evaluate(self, **kwargs):
            return {
                "answers": {
                    "action": {"type": "choice", "choice": "allow"},
                    "reason_code": {"type": "choice", "choice": "no_issue"},
                }
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    guard = make_tool_call_guard({})
    assert guard(tool_name="read_file", args={}) is None
    assert captured["timeout_seconds"] == 25.0

    captured.clear()
    guard = make_tool_call_guard({"timeout_seconds": 10})
    assert guard(tool_name="read_file", args={}) is None
    assert captured["timeout_seconds"] == 10.0


def test_tool_call_guard_blocks_contradictory_allow_and_reason(monkeypatch):
    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            return {
                "answers": {
                    "action": {"type": "choice", "choice": "allow"},
                    "reason_code": {"type": "choice", "choice": "destructive_change"},
                }
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    guard = make_tool_call_guard({})
    result = guard(tool_name="terminal", args={"command": "rm -rf ./data"})
    assert result["action"] == "block"
    assert "invalid pre-tool decision" in result["message"]


def test_tool_call_guard_escalates_review_and_deny_to_human(monkeypatch):
    decisions = iter(
        (
            ("review", "deployment_or_release"),
            ("deny", "destructive_change"),
        )
    )

    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            action, reason_code = next(decisions)
            return {
                "answers": {
                    "action": {"type": "choice", "choice": action},
                    "reason_code": {"type": "choice", "choice": reason_code},
                }
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    guard = make_tool_call_guard({})
    review = guard(tool_name="terminal", args={"command": "rm -rf ./build"})
    deny = guard(tool_name="write_file", args={"path": "out.txt", "content": "data"})
    assert review["action"] == "block"
    assert "review" in review["message"]
    assert "deployment_or_release" in review["message"]
    assert _TOOL_CALL_REASON_CODES["deployment_or_release"] in review["message"]
    assert deny["action"] == "block"
    assert "deny" in deny["message"]
    assert "destructive_change" in deny["message"]


def test_tool_call_guard_blocks_jev_failure_without_leaking_details(monkeypatch):
    class BrokenClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            raise RuntimeError("secret-token-leak")

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", BrokenClient)
    guard = make_tool_call_guard({})
    result = guard(tool_name="terminal", args={"command": "pwd"})
    assert result["action"] == "block"
    assert "secret-token-leak" not in result["message"]


def _allowing_client(captured):
    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            captured.update(kwargs)
            return {
                "answers": {
                    "action": {"type": "choice", "choice": "allow"},
                    "reason_code": {"type": "choice", "choice": "no_issue"},
                }
            }

    return FakeClient


def test_action_instructions_distinguish_local_tests_from_deploys():
    text = _TOOL_CALL_QUESTIONS["action"]["instructions"]
    assert "local_file_excerpts" in text
    assert "deploy" in text.lower()
    assert "test harness" in text.lower()


def test_normalize_hook_cwd_accepts_plain_and_file_urls():
    assert normalize_hook_cwd("/home/fagnersouza/Projetos/dividindo-tudo") == (
        "/home/fagnersouza/Projetos/dividindo-tudo"
    )
    assert (
        normalize_hook_cwd("file:///home/fagnersouza/Projetos/dividindo-tudo")
        == "/home/fagnersouza/Projetos/dividindo-tudo"
    )
    assert normalize_hook_cwd(None) == ""
    assert normalize_hook_cwd(123) == ""


def test_hermes_guard_does_not_read_local_files(tmp_path, monkeypatch):
    script = tmp_path / "deploy-backend_test.sh"
    script.write_text("#!/bin/bash\necho should-not-be-sent\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", _allowing_client(captured))
    guard = make_tool_call_guard({})
    assert guard(
        tool_name="Bash",
        args={"command": "./deploy-backend_test.sh"},
        cwd=str(tmp_path),
    ) is None
    assert "local_file_excerpts" not in captured["state"]
    assert "should-not-be-sent" not in json.dumps(captured["state"])


def test_codex_guard_attaches_cwd_script_excerpt(tmp_path, monkeypatch):
    script = tmp_path / "deploy-backend_test.sh"
    script.write_text(
        "#!/usr/bin/env bash\n# local test harness\nassert_contem() { :; }\n",
        encoding="utf-8",
    )
    captured = {}
    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", _allowing_client(captured))
    guard = make_tool_call_guard({}, attach_local_files=True)
    assert guard(
        tool_name="Bash",
        args={"command": "./deploy-backend_test.sh"},
        cwd=str(tmp_path),
    ) is None
    excerpts = captured["state"]["local_file_excerpts"]
    assert excerpts[0]["path"] == "deploy-backend_test.sh"
    assert excerpts[0]["looks_like_test"] is True
    assert "assert_contem" in excerpts[0]["excerpt"]
    assert "local test harness" in excerpts[0]["excerpt"]


def test_codex_guard_skips_secrets_and_paths_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "ok.sh").write_text("echo ok\n", encoding="utf-8")
    (workspace / ".env").write_text("TYPESAFE_API_KEY=do-not-send\n", encoding="utf-8")
    outside = tmp_path / "secret.sh"
    outside.write_text("export AWS_SECRET_ACCESS_KEY=do-not-send\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", _allowing_client(captured))
    guard = make_tool_call_guard({}, attach_local_files=True)
    assert guard(
        tool_name="Bash",
        args={"command": "bash ok.sh .env ../secret.sh"},
        cwd=str(workspace),
    ) is None
    serialized = json.dumps(captured["state"])
    assert "do-not-send" not in serialized
    excerpts = captured["state"].get("local_file_excerpts", [])
    assert [item["path"] for item in excerpts] == ["ok.sh"]


def test_codex_guard_skips_symlink_pointing_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.sh"
    outside.write_text("export AWS_SECRET_ACCESS_KEY=do-not-send\n", encoding="utf-8")
    (workspace / "run.sh").symlink_to(outside)
    captured = {}
    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", _allowing_client(captured))
    guard = make_tool_call_guard({}, attach_local_files=True)
    assert guard(
        tool_name="Bash",
        args={"command": "./run.sh"},
        cwd=str(workspace),
    ) is None
    serialized = json.dumps(captured["state"])
    assert "do-not-send" not in serialized
    assert "local_file_excerpts" not in captured["state"]


def test_tool_call_guard_invalid_reason_code_still_blocks(monkeypatch):
    class FakeClient:
        @classmethod
        def from_settings(cls, settings, **kwargs):
            return cls()

        def evaluate(self, **kwargs):
            return {
                "answers": {
                    "action": {"type": "choice", "choice": "review"},
                    "reason_code": {"type": "choice", "choice": "not_a_real_code"},
                }
            }

    monkeypatch.setattr("jev_plugin_for_hermes.tools.JevClient", FakeClient)
    guard = make_tool_call_guard({})
    result = guard(tool_name="Bash", args={"command": "./infra/scripts/deploy-backend_test.sh"})
    assert result["action"] == "block"
    assert "invalid" in result["message"].lower()
