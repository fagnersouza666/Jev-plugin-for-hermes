"""Offline contracts for the external gate; collectors and jobs never run here."""

from __future__ import annotations

import copy
import io
import json

import pytest
from jev_plugin_for_hermes.client import PluginSettings, _parse_answers, build_request
from jev_plugin_for_hermes.cron_gate import assess_gate, main
from jev_plugin_for_hermes.tools import _PRICE_QUESTIONS


def payload():
    return {"target": "GPU model", "offers": [
        {"id": "offer-1", "eligible": True, "offer": {"title": "GPU model"}},
        {"id": "offer-2", "eligible": False, "offer": {"title": "Different model"}},
    ]}


class Transport:
    def __init__(self, recommendation="alert", *, error=False, delay=0):
        self.recommendation = recommendation
        self.error = error
        self.delay = delay
        self.now = 0
        self.calls = []
        self.timeouts = []

    def factory(self, settings):
        parent = self

        class Client:
            def evaluate(self, *, state, questions, model):
                parent.calls.append(build_request(state, questions, model, settings.max_state_chars).as_payload())
                parent.timeouts.append(settings.timeout_seconds)
                parent.now += parent.delay
                if parent.error:
                    raise RuntimeError("private provider error")
                answers = {}
                for name, question in questions.items():
                    kind = question["type"]
                    value = (parent.recommendation if name == "recommendation" else
                             next(iter(question["criteria"])) if kind == "choice" else 0.8)
                    answers[name] = {"type": kind, kind: value}
                return _parse_answers(json.dumps({"answers": answers}).encode(), 200, questions)

        return Client()


@pytest.mark.parametrize("mode", ["off", "observe", "active"])
@pytest.mark.parametrize("recommendation", ["alert", "manual_review", "record", "ignore"])
def test_gate_modes_recommendations_and_collector_eligibility(mode, recommendation):
    transport = Transport(recommendation)
    original = payload()
    snapshot = copy.deepcopy(original)
    result = assess_gate(original, PluginSettings(), mode=mode, client_factory=transport.factory)
    expected = recommendation in {"alert", "manual_review"}
    assert result["ok"] is True
    assert result["wakeAgent"] is (True if mode != "active" else expected)
    assert result["wouldWakeAgent"] is (True if mode == "off" else expected)
    assert [item["id"] for item in result["candidates"]] == ["offer-1"]
    assert original == snapshot
    assert len(transport.calls) == (0 if mode == "off" else 1)
    if transport.calls:
        assert transport.calls[0]["questions"] == _PRICE_QUESTIONS


@pytest.mark.parametrize("case", ["missing", "duplicate", "eligibility", "too_many", "oversized", "nan"])
def test_invalid_gate_input_never_evaluates_or_wakes(case):
    original = payload()
    if case == "missing":
        del original["target"]
    elif case == "duplicate":
        original["offers"].append(copy.deepcopy(original["offers"][0]))
    elif case == "eligibility":
        original["offers"][0]["eligible"] = "true"
    elif case == "too_many":
        original["offers"] = [{"id": f"offer-{i}", "eligible": True, "offer": {"title": "GPU"}}
                              for i in range(33)]
    else:
        original["offers"][0]["offer"]["extra"] = float("nan") if case == "nan" else "x" * 200_001
    transport = Transport()
    assert assess_gate(original, PluginSettings(), client_factory=transport.factory) == {
        "ok": False, "wakeAgent": False, "error_code": "invalid_input",
    }
    assert not transport.calls


def test_gate_without_eligible_offers_never_calls_provider():
    original = payload()
    original["offers"][0]["eligible"] = False
    transport = Transport()
    result = assess_gate(original, PluginSettings(), mode="active", client_factory=transport.factory)
    assert result["wakeAgent"] is False
    assert result["candidates"] == []
    assert not transport.calls


@pytest.mark.parametrize("case", ["provider_error", "malformed", "late"])
def test_assessment_failure_wakes_only_eligible_offers_for_review(case, caplog):
    transport = Transport("unexpected" if case == "malformed" else "ignore",
                          error=case == "provider_error", delay=25 if case == "late" else 0)
    result = assess_gate(payload(), PluginSettings(), mode="active", client_factory=transport.factory,
                         clock=lambda: transport.now)
    assert result["wakeAgent"] is True
    assert result["candidates"] == [{"id": "offer-1", "wouldWakeAgent": True,
                                      "reason_code": "assessment_failed_review"}]
    assert "private provider error" not in caplog.text + json.dumps(result)


def test_gate_budget_exhaustion_skips_remaining_assessments_and_wakes():
    original = payload()
    original["offers"][1]["eligible"] = True
    transport = Transport("ignore", delay=24.5)
    result = assess_gate(original, PluginSettings(), mode="active", client_factory=transport.factory,
                         clock=lambda: transport.now)
    assert len(transport.calls) == 1
    assert transport.timeouts == [25]
    assert result["wakeAgent"] is True
    assert result["candidates"][0]["wouldWakeAgent"] is False
    assert result["candidates"][1]["reason_code"] == "assessment_failed_review"


@pytest.mark.parametrize("raw", ["{", "x" * 200_001, '{"target":"GPU","offers":[]}'])
def test_cli_emits_json_and_zero_exit_so_hermes_honors_no_wake(raw):
    output = io.StringIO()
    assert main([], stdin=io.StringIO(raw), stdout=output) == 0
    assert json.loads(output.getvalue())["wakeAgent"] is False


def test_gate_preserves_late_listing_evidence_and_redacts_credentials():
    original = payload()
    offer = original["offers"][0]["offer"]
    offer["description"] = "GPU specifications. " * 60 + "Does not power on."
    offer["observations"] = [f"Evidence {i}" for i in range(80)]
    offer["seller"] = {"details": {"history": {"condition": "For parts only"}},
                       "api_key": "private-key", "notes": "Bearer private-token"}
    transport = Transport("manual_review")
    result = assess_gate(original, PluginSettings(), mode="active", client_factory=transport.factory)
    sent = json.loads(transport.calls[0]["state"])["offer"]
    assert sent["description"] == offer["description"]
    assert sent["observations"] == offer["observations"]
    assert sent["seller"]["details"] == offer["seller"]["details"]
    assert sent["seller"]["api_key"] == "[REDACTED]"
    assert "private-token" not in json.dumps(sent)
    assert result["wakeAgent"] is True


def test_gate_oversized_state_wakes_for_review_without_evaluating_partial_evidence():
    original = payload()
    original["offers"][0]["offer"]["description"] = "x" * 20_001
    transport = Transport("ignore")
    result = assess_gate(original, PluginSettings(), mode="active", client_factory=transport.factory)
    assert not transport.calls
    assert result["wakeAgent"] is True
    assert result["candidates"][0]["reason_code"] == "assessment_failed_review"
