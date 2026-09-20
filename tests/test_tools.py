import json

from jev_plugin_for_hermes.tools import make_handlers


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
