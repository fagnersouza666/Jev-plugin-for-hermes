"""Offline routing tests with real request/response validation."""

from __future__ import annotations

import copy
import json

import pytest
from jev_plugin_for_hermes.client import PluginSettings, _parse_answers, build_request
from jev_plugin_for_hermes.routing import AdvisoryRouter

IDS = {"session_id": "session", "task_id": "task", "turn_id": "turn"}
CATALOG = [{"id": "requirements", "description": "Analyze requirements", "details": "Acceptance criteria."},
           {"id": "research", "description": "Research sources", "details": "Cite evidence."},
           {"id": "godot", "description": "Implement Godot games", "details": "Inspect scenes."},
           {"id": "testing", "description": "Test a delivery", "details": "Check criteria."}]


class Transport:
    def __init__(self):
        self.calls = []
        self.timeouts = []
        self.now = 0.0
        self.delay = 0.0
        self.error = None
        self.selected = "s0"
        self.applicable = 0.9
        self.relevant = {0}
        self.probabilities = None
        self.strategy = "missing_data"

    def factory(self, settings):
        parent = self

        class Client:
            def evaluate(self, *, state, questions, model):
                request = build_request(state, questions, model, settings.max_state_chars)
                parent.calls.append(request.as_payload())
                parent.timeouts.append(settings.timeout_seconds)
                parent.now += parent.delay
                if parent.error:
                    raise parent.error
                answers = {}
                for name, question in questions.items():
                    kind = question["type"]
                    if name == "rank":
                        keys = list(question["criteria"])
                        probs = parent.probabilities
                        if probs is None:
                            probs = {key: 1 / len(keys) for key in keys}
                        answers[name] = {"type": kind, "choice": keys[0], "probabilities": probs}
                    elif name == "applicable":
                        answers[name] = {"type": kind, "noul": parent.applicable}
                    elif name == "strategy":
                        answers[name] = {"type": kind, "choice": parent.strategy}
                    elif kind == "choice":
                        answers[name] = {"type": kind, "choice": parent.selected}
                    else:
                        answers[name] = {"type": kind, "noul": 0.9 if int(name[1:]) in parent.relevant else 0.1}
                return _parse_answers(json.dumps({"answers": answers}).encode(), 200, questions)

        return Client()


def router_for(**config):
    transport = Transport()
    router = AdvisoryRouter(PluginSettings.from_mapping(config), client_factory=transport.factory,
                            clock=lambda: transport.now)
    return router, transport


def start(router, **ids):
    return router.pre_llm_call(**{**IDS, **ids}, user_message="Help with acceptance criteria")


def schema(name, responses=False):
    function = {"name": name, "description": "A useful tool", "parameters": {"type": "object"}}
    return {"type": "function", **function} if responses else {"type": "function", "function": function}


@pytest.mark.parametrize("mode", ["observe", "suggest"])
def test_skill_ranking_top_three_none_and_retry_cache(mode):
    router, transport = router_for(skills_mode=mode, skills_catalog=CATALOG)
    transport.probabilities = {"s0": 0.6, "s1": 0.2, "s2": 0.15, "s3": 0.05}
    result = start(router)
    assert (result is not None) == (mode == "suggest")
    if result:
        assert "requirements" in result["context"]
    assert set(transport.calls[1]["questions"]["selection"]["criteria"]) == {"s0", "s1", "s2", "none"}
    assert start(router) == result
    assert len(transport.calls) == 2
    transport.selected = "none"
    assert start(router, turn_id="next") is None


def test_large_catalog_not_truncated_and_ties_use_identifiers():
    entries = [{"id": f"skill-{i:03}", "description": f"Skill {i}"} for i in range(181, -1, -1)]
    router, transport = router_for(skills_mode="suggest", skills_catalog=entries)
    transport.selected = "s181"
    assert "skill-000" in start(router)["context"]
    assert len(transport.calls[0]["questions"]["rank"]["criteria"]) == 182
    assert set(transport.calls[1]["questions"]["selection"]["criteria"]) == {"s179", "s180", "s181", "none"}


def test_skill_applicability_has_the_catalog_in_its_own_state():
    router, transport = router_for(skills_mode="suggest", skills_catalog=CATALOG)
    assert start(router) is not None
    state = json.loads(transport.calls[0]["state"])
    assert state["skills"] == {f"s{i}": entry["description"] for i, entry in enumerate(CATALOG)}
    assert state["request"] == "Help with acceptance criteria"


def test_recovery_success_clears_advice_without_reactivating_cached_strategy():
    router, transport = router_for(recovery_mode="suggest")
    start(router)
    for i in range(3):
        router.post_tool_call(**IDS, tool_name="web_search", tool_call_id=f"failure-{i}", status="error")
    request = {"input": "Find another source"}
    assert router.llm_request(**IDS, request=request) is not None
    router.post_tool_call(**IDS, tool_name="web_search", tool_call_id="success", status="ok")
    assert router.llm_request(**IDS, request=request) is None
    for i in range(3):
        router.post_tool_call(**IDS, tool_name="web_search", tool_call_id=f"later-{i}", status="error")
    assert router.llm_request(**IDS, request=request) is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("case", ["empty", "incomplete", "irrelevant", "oversized", "failure", "late"])
def test_skill_abstention_keeps_flow_and_caches_failure(case):
    config = {"skills_mode": "suggest", "skills_catalog": CATALOG}
    if case == "empty":
        config["skills_catalog"] = []
    if case == "oversized":
        config["skills_catalog"] = [{"id": f"s{i}", "description": "x" * 4096} for i in range(64)]
    router, transport = router_for(**config)
    if case == "incomplete":
        transport.probabilities = {"s0": 0.5}
    if case == "irrelevant":
        transport.applicable = 0.1
    if case == "failure":
        transport.error = RuntimeError("secret raw provider error")
    if case == "late":
        transport.delay = 25
    assert start(router) is None
    count = len(transport.calls)
    assert start(router) is None
    assert len(transport.calls) == count


def test_profile_advice_and_keep_current():
    router, transport = router_for(profiles_mode="suggest", profiles_catalog=CATALOG)
    transport.selected = "p3"
    assert "testing" in start(router)["context"]
    transport.selected = "keep"
    assert start(router, turn_id="next") is None


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("mode", ["off", "observe", "filter"])
def test_tools_protected_native_and_request_not_mutated(responses, mode):
    router, transport = router_for(tools_mode=mode, essential_tools=["important"])
    start(router)
    names = ["godot", "irrelevant", "skills_list", "skill_view", "forced", "important"]
    request = {"tools": [schema(name, responses) for name in names] + [{"type": "web_search"}],
               "tool_choice": {"type": "function", "name": "forced"} if responses else
                              {"type": "function", "function": {"name": "forced"}},
               "model": "unchanged"}
    original = copy.deepcopy(request)
    result = router.llm_request(**IDS, request=request)
    assert request == original
    if mode == "filter":
        assert result["request"]["tools"] == [request["tools"][i] for i in (0, 2, 3, 4, 5, 6)]
        assert result["request"]["model"] == "unchanged"
    else:
        assert result is None
    assert len(transport.calls) == (0 if mode == "off" else 1)
    router.llm_request(**IDS, request=request)
    assert len(transport.calls) == (0 if mode == "off" else 1)
    request["tools"].append(schema("new", responses))
    router.llm_request(**IDS, request=request)
    assert len(transport.calls) == (0 if mode == "off" else 2)


def test_empty_tool_selection_and_incomplete_batch_keep_all():
    router, transport = router_for(tools_mode="filter")
    start(router)
    transport.relevant = set()
    assert router.llm_request(**IDS, request={"tools": [schema("a")]}) is None
    transport.relevant = {0}
    transport.delay = 13
    assert router.llm_request(**IDS, request={"tools": [schema(f"t{i}") for i in range(33)]}) is None
    assert len(transport.calls) == 3
    assert transport.timeouts[-1] == 12


def test_budget_is_shared_between_features():
    router, transport = router_for(skills_mode="suggest", skills_catalog=CATALOG, tools_mode="filter")
    transport.delay = 12.1
    assert start(router) is not None
    assert router.llm_request(**IDS, request={"tools": [schema("a")]}) is None
    assert len(transport.calls) == 2


@pytest.mark.parametrize("name", ["web_search", "session_search"])
@pytest.mark.parametrize("mode", ["off", "observe", "filter"])
def test_result_selection_preserves_complete_evidence_and_order(name, mode):
    router, transport = router_for(results_mode=mode)
    start(router)
    transport.relevant = {0, 2}
    items = [{"title": f"Source {i}", "snippet": "Evidence", "url": f"https://example.org/{i}",
              "citation": {"id": i}, "extra": [i]} for i in range(3)]
    body = ({"success": True, "data": {"web": items, "other": "preserved"}} if name == "web_search" else
            {"success": True, "mode": "discover", "results": items, "count": 3})
    raw = json.dumps(body)
    result = router.transform_tool_result(**IDS, tool_name=name, status="ok", result=raw)
    if mode == "filter":
        expected = copy.deepcopy(body)
        container = expected["data"] if name == "web_search" else expected
        container["web" if name == "web_search" else "results"] = [items[0], items[2]]
        expected["jev_filter"] = {"original_count": 3, "omitted": 1}
        if name == "session_search":
            expected["count"] = 2
        assert json.loads(result) == expected
    else:
        assert result is None
    assert router.transform_tool_result(**IDS, tool_name=name, status="ok", result=raw) == result
    assert len(transport.calls) == (0 if mode == "off" else 1)


@pytest.mark.parametrize("case", ["unknown", "error", "malformed", "unsuccessful", "empty", "failure", "none"])
def test_result_selection_abstains_without_replacing_original(case):
    router, transport = router_for(results_mode="filter")
    start(router)
    body = {"success": case != "unsuccessful", "data": {"web": [{"title": "Evidence"}]}}
    if case == "empty":
        body["data"]["web"] = []
    if case == "failure":
        transport.error = RuntimeError("private provider error")
    if case == "none":
        transport.relevant = set()
    assert router.transform_tool_result(
        **IDS, tool_name="unknown" if case == "unknown" else "web_search",
        status="error" if case == "error" else "ok",
        result="{" if case == "malformed" else json.dumps(body),
    ) is None
    assert len(transport.calls) == (1 if case in {"failure", "none"} else 0)


@pytest.mark.parametrize("mode", ["off", "observe", "suggest"])
@pytest.mark.parametrize("provider_field", ["messages", "input"])
def test_recovery_counts_deduplicates_resets_and_only_suggests(mode, provider_field):
    router, transport = router_for(recovery_mode=mode)
    start(router)

    def event(identifier, status="error", tool="web_search"):
        assert router.post_tool_call(**IDS, tool_name=tool, tool_call_id=identifier, status=status,
                                     error_type="TimeoutError", error_message="private error body") is None

    event("first")
    event("first")
    event("second")
    event("success", "ok")
    event("third")
    event("blocked", "blocked")
    event("cancelled", "cancelled")
    event("fourth")
    event("other-tool", tool="session_search")
    assert not transport.calls
    event("fifth")
    event("sixth")
    assert len(transport.calls) == (0 if mode == "off" else 1)
    assert "private error body" not in json.dumps(transport.calls)
    request = {provider_field: [{"role": "user", "content": "Find evidence"}], "model": "unchanged"}
    original = copy.deepcopy(request)
    result = router.llm_request(**IDS, request=request)
    assert request == original
    if mode == "suggest":
        updated = result["request"]
        assert updated[provider_field][:-1] == request[provider_field]
        assert "missing information" in updated[provider_field][-1]["content"]
        assert updated["model"] == request["model"]
        assert router.llm_request(**IDS, request=updated) is None
    else:
        assert result is None


def test_turn_isolation_expiry_and_concurrent_callback_skip():
    router, transport = router_for(skills_mode="suggest", skills_catalog=CATALOG)
    assert start(router) is not None
    state = next(iter(router.turns.values()))
    with state.lock:
        assert start(router) is None
    assert len(transport.calls) == 2
    assert start(router, session_id="another-session") is not None
    assert len(transport.calls) == 4
    transport.now = 901
    assert start(router) is not None
    assert len(transport.calls) == 6
    assert len(router.turns) == 1
