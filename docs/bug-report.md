# Bug Report — jev-plugin-for-hermes

> Date: 20/09/2026 | Stack: Python 3.11+ Hermes standalone plugin (stdlib `urllib`, no runtime deps)
> Mode: **full**
> Files analyzed: 8 Python modules (`client.py`, `tools.py`, `__init__.py`, `schemas.py`, `tests/conftest.py`, `tests/test_client.py`, `tests/test_tools.py`, `tests/test_registration.py`) plus `plugin.yaml`, `pyproject.toml`, and `skills/jev-playbook/SKILL.md`

**Graph (codebase-memory):** `list_projects`, `index_status`, `get_architecture`, `check_index_coverage`, `search_graph`. Hermes hook/command contract: `get_code_snippet` on `hermes-agent` (`PluginContext.register_command`, `_get_pre_tool_call_directive_details`, `PluginDispatchMixin.invoke_hook`).

**Index coverage:** project `jev-plugin-for-hermes` ready (328 nodes, 978 edges, 0 call cycles). Cited paths had `no_recorded_issue` / `metadata_match` at index time `2026-09-20T20:23:08Z`. `parse_partial` and `skipped` were empty. Best-effort only — not a completeness proof. `__pycache__` / `.venv` are excluded by design.

---

## Sumário

| Severidade | Quantidade |
|------------|------------|
| CRITICO    | 0          |
| ALTO       | 0          |
| MEDIO      | 0          |
| BAIXO      | 0          |
| **Total**  | **0**      |

**Veredicto:** OK

All open findings from this snapshot (BUG-012 through BUG-018) are **resolved** in the current tree. Handlers and the hook fail closed on missing keys, HTTP errors, malformed answer bodies, contradictory pre-tool decisions, and oversized or mistyped optional vendor fields.

Incremental scan 20/09/2026 (pip-audit / setuptools gate): `scripts/pip-audit.sh`, `scripts/install-git-hooks.sh`, `.githooks/pre-commit`, `tests/test_security_gate.py`, `pyproject.toml`, `.github/workflows/ci.yml`. **0 findings.** `git config --local` only; venv-scoped pip; fail closed if the audit cannot run.

Historical BUG-001 through BUG-011 (19/09/2026) stay **mitigated**. See the appendix.

---

## CRITICO

None. No null-deref, SQL injection, or always-on crash in the tool loop. Unexpected exceptions become `{ok: false, error.code: internal_error}` or a hook `block` directive.

---

## ALTO

None.

### BUG-012: `pre_tool_call` allows a call when `reason_code` contradicts `allow` — **Resolved**

**Arquivo:** `tools.py`
**Linha(s):** 111-118, 408-423

**O que acontece:**
The hook asks two independent choice questions: `action` (`allow` / `review` / `deny`) and `reason_code` (including `no_issue`, `destructive_change`, `deployment_or_release`, …). Instructions say to use `no_issue` for `allow`. After a successful `JevClient.evaluate`, the guard returns `None` (Hermes allow) as soon as `action == "allow"`, without checking `reason_code`.

`review`/`deny` with a valid reason still blocks. `allow` plus any valid reason, including `destructive_change`, proceeds.

**Por que é um problema:**
`AGENTS.md` maps malformed pre-tool decisions to Hermes' blocking directive. A contradictory pair is a valid wire payload (both values are in the criteria) but not a coherent decision. Scenario: Jev returns `allow` + `destructive_change` for `terminal` / `rm -rf ./data`. The plugin leaves the normal approval path untouched instead of blocking. That is fail-open on the only security hook this plugin registers.

**Código problemático:**

```python
answers = response["answers"]
action = answers["action"]["choice"]
reason_code = answers["reason_code"]["choice"]
# ...
if action == "allow":
    return None
```

**Solução:**

```python
if action == "allow" and reason_code == "no_issue":
    return None
if action in {"review", "deny"} and reason_code != "no_issue":
    reason = _TOOL_CALL_REASON_CODES.get(reason_code)
    if reason is None:
        return {
            "action": "block",
            "message": (
                f"Jev blocked tool '{tool_name}' before execution ({action}); "
                "reason: invalid"
            ),
        }
    return {
        "action": "block",
        "message": (
            f"Jev blocked tool '{tool_name}' before execution ({action}); "
            f"reason: {reason_code} — {reason}"
        ),
    }
return {
    "action": "block",
    "message": "Jev returned an invalid pre-tool decision; the tool call was blocked.",
}
```

Add a unit test that `allow` + `destructive_change` returns `action: block`.

**Explicação da correção:**
`allow` is only a pass-through when the reason is `no_issue`. Any other pairing is treated as a malformed decision and blocked, matching the fail-closed hook contract.

**Status now:** `_tool_call_guard` requires `allow` + `no_issue`; `test_tool_call_guard_blocks_contradictory_allow_and_reason` covers `allow` + `destructive_change`.

---

## MEDIO

None.

### BUG-013: Choice/score `confidence`, `probabilities`, and `legend` are validated then discarded — **Resolved**

**Arquivo:** `client.py`
**Linha(s):** 355-375
**Arquivo:** `skills/jev-playbook/SKILL.md`
**Linha(s):** 25, 33
**Arquivo:** `__init__.py`
**Linha(s):** 50

**O que acontece:**
TypeSafe Choice/Score answers include calibrated `confidence` and `probabilities` (Score also includes `legend`). `_validate_answer_metadata` accepts those fields, then `_validate_answer_body` returns only `{type, choice}` or `{type, score}`. `test_client_accepts_current_choice_and_score_metadata` asserts the drop. Noul stays `{type, noul}` only, which matches the vendor contract (noul has no confidence).

The registered skill still tells the agent to treat confidence/probabilities as evidence and to send intermediate confidence to human review. The skill metadata promises "confidence checks".

**Por que é um problema:**
The first intended consumer is a price-monitor pipeline. After a successful `jev_price_assess` / `jev_evaluate`, the model and any downstream policy cannot see the calibrated fields the API actually returned. Playbook step 5 ("human review when … confidence is intermediate") cannot be implemented from the tool result. This is not a crash; it is a silent loss of the vendor's decision quality signal.

**Código problemático:**

```python
if kind == "choice":
    _validate_answer_metadata(body, {"type", "choice", "confidence", "probabilities"})
    # ...
    return {"type": "choice", "choice": choice}

if kind == "score":
    _validate_answer_metadata(body, {"type", "score", "confidence", "legend", "probabilities"})
    # ...
    return {"type": "score", "score": value}
```

**Solução:**

```python
if kind == "choice":
    _validate_answer_metadata(body, {"type", "choice", "confidence", "probabilities"})
    if "choice" not in body:
        raise JevProtocolError("Jev API response did not contain an answers object")
    criteria = expected_question["criteria"]
    choice = body["choice"]
    if not isinstance(choice, str) or choice not in criteria:
        raise JevProtocolError("Jev API response did not contain an answers object")
    out: dict[str, Any] = {"type": "choice", "choice": choice}
    if "confidence" in body:
        out["confidence"] = body["confidence"]
    if "probabilities" in body:
        out["probabilities"] = dict(body["probabilities"])
    return out
```

Mirror the same copy for score (`legend` included). Keep noul as `{type, noul}`. Update `test_client_accepts_current_choice_and_score_metadata` to expect the allowlisted metadata.

**Explicação da correção:**
The plugin still owns the envelope and still rejects unknown keys. Official TypeSafe fields become visible so the playbook's confidence checks can run, without reopening vendor `ok`/`error` overwrite.

**Status now:** `_copy_validated_answer_metadata` forwards allowlisted fields; `test_client_accepts_current_choice_and_score_metadata` expects them.

---

### BUG-014: `usage` and `model` are copied with almost no schema — **Resolved**

**Arquivo:** `client.py`
**Linha(s):** 404-418

**O que acontece:**
After answers are validated, `_parse_answers` copies `model` if it is a non-empty string, and `usage` if it is any JSON-serializable dict. There is no key allowlist, no numeric check, and no separate size cap beyond the 1 MiB raw body. Those fields are then placed on the tool envelope in `_call_jev`.

TypeSafe documents `usage` as `{input_tokens, output_tokens}` and `model` as a short alias such as `jev-1.13.0`.

**Por que é um problema:**
`api_url` is an operator trust boundary, so a hostile host is not treated as SSRF via tool arguments. A buggy or compromised gateway that still returns valid `answers` can attach a 1 MiB `usage` object or a long `model` string. That text lands in the agent context (prompt-injection / noise) while `{ok: true}` stays set. Answers remain fail-closed; the optional fields are not.

**Código problemático:**

```python
if "model" in decoded:
    model = decoded["model"]
    if not isinstance(model, str) or not model.strip():
        raise JevProtocolError("Jev API response did not contain an answers object")
    result["model"] = model.strip()

if "usage" in decoded:
    usage = decoded["usage"]
    if not isinstance(usage, dict):
        raise JevProtocolError("Jev API response did not contain an answers object")
    try:
        json.dumps(usage, **_JSON_DUMP_KWARGS)
    except (TypeError, ValueError) as exc:
        raise JevProtocolError("Jev API response did not contain an answers object") from exc
    result["usage"] = usage
```

**Solução:**

```python
_MAX_MODEL_CHARS = 128
_USAGE_KEYS = {"input_tokens", "output_tokens"}

if "model" in decoded:
    model = decoded["model"]
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > _MAX_MODEL_CHARS:
        raise JevProtocolError("Jev API response did not contain an answers object")
    result["model"] = model.strip()

if "usage" in decoded:
    usage = decoded["usage"]
    if not isinstance(usage, dict) or not set(usage).issubset(_USAGE_KEYS):
        raise JevProtocolError("Jev API response did not contain an answers object")
    cleaned: dict[str, int] = {}
    for key, value in usage.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise JevProtocolError("Jev API response did not contain an answers object")
        cleaned[key] = value
    result["usage"] = cleaned
```

**Explicação da correção:**
Optional vendor fields then match the documented System One shape. Unknown usage keys fail closed instead of entering the agent transcript.

**Status now:** `_MAX_MODEL_CHARS = 128`, `_USAGE_KEYS` allowlist, and strict int validation in `_parse_answers`; extended tests in `test_parse_answers_rejects_invalid_model_and_usage`.

---

### BUG-015: Hook Jev timeout equals Hermes `pre_tool_call` callback timeout — **Resolved**

**Arquivo:** `client.py`
**Linha(s):** 70-91
**Arquivo:** `tools.py`
**Linha(s):** 448-455
**Arquivo:** `__init__.py`
**Linha(s):** 29

**O que acontece:**
`PluginSettings` defaults `timeout_seconds` to 30.0 (clamped 1–600). `make_tool_call_guard` builds a `JevClient` with that timeout. Hermes runs `pre_tool_call` under `plugins.hook_callback_timeout` (default 30s, fail-closed). On timeout Hermes abandons the worker thread (never joined) and injects its own `block` directive.

The plugin's urllib timeout and the host hook timeout are therefore the same number. DNS + connect + POST + parse can exceed 30s wall clock even when the socket timeout is 30s.

**Por que é um problema:**
A slow TypeSafe call does not return the plugin's `Jev pre-tool assessment failed` message. Hermes kills the callback, leaves `urlopen` running on an abandoned thread, and blocks the tool with the generic hook-timeout text. Under a busy session that can accumulate sockets until the abandoned reads finish. Fail-closed still holds (the tool does not run); the leak and the opaque message are the defect.

**Código problemático:**

```python
tool_call_guard = tools.make_tool_call_guard(settings)
# settings.timeout_seconds default 30.0 — same as Hermes hook_callback_timeout
```

**Solução:**

```python
def make_tool_call_guard(
    settings: PluginSettings | Mapping[str, Any],
    *,
    attach_local_files: bool = False,
):
    plugin_settings = _resolve_settings(settings)
    hook_timeout = max(1.0, min(plugin_settings.timeout_seconds, 25.0))
    hook_settings = PluginSettings(
        api_url=plugin_settings.api_url,
        default_model=plugin_settings.default_model,
        timeout_seconds=hook_timeout,
        max_state_chars=plugin_settings.max_state_chars,
    )
    client = JevClient.from_settings(hook_settings)
    # ... register guard with hook_settings ...
```

Keep the 30s default for `jev_evaluate` / `jev_price_assess` / `/jev`. Document that the hook uses a tighter cap so it finishes before Hermes abandons the worker. Add a test that `make_tool_call_guard` passes a timeout `< 30` when settings use the default.

**Explicação da correção:**
The hook then raises `JevTransportError` (mapped to a plugin block message) while the worker is still joined by Hermes, instead of racing the host's 30s abandon path.

**Status now:** `make_tool_call_guard` uses `dataclasses.replace` with `_HOOK_TIMEOUT_MAX = 25.0`; `test_make_tool_call_guard_uses_tighter_timeout_than_default_settings` covers default and custom settings.

---

## BAIXO

None.

### BUG-016: Choice criteria keys can collide after `strip()` — **Resolved**

**Arquivo:** `client.py`
**Linha(s):** 136-145

**O que acontece:**
Score labels are stripped into a list (duplicates stay as separate ranks). Choice keys are stripped into a dict. `{"a": "one", " a ": "two"}` becomes `{"a": "two"}` with no error.

**Por que é um problema:**
A model-generated criteria object with padded duplicate names silently drops a category. Downstream `choice in criteria` then rejects a vendor label that the caller thought it sent. Low likelihood because the usual caller is the LLM and keys are short identifiers.

**Solução:**

```python
cleaned = {k.strip(): v.strip() for k, v in criteria.items()}
if len(cleaned) != len(criteria):
    raise JevValidationError(f"choice question {name!r} criteria keys must be unique after stripping")
return cleaned
```

**Explicação da correção:**
Collision becomes a local `JevValidationError` instead of a smaller question set on the wire.

**Status now:** `_validate_choice_criteria` rejects collisions; `test_build_request_rejects_choice_criteria_that_collide_after_strip` covers the case.

---

### BUG-017: `register()` `description=` is not the schema description the model reads — **Resolved**

**Arquivo:** `__init__.py`
**Linha(s):** 30-42
**Arquivo:** `schemas.py`
**Linha(s):** 29-61

**O que acontece:**
`AGENTS.md` requires `schema["description"]` and the optional `description=` metadata to stay in sync. They do not:

- `jev_evaluate` metadata: `"Evaluate evidence with TypeSafe Jev primitives."` vs a longer schema text that names Noul/Choice/Score and forbids irreversible authorization.
- `jev_price_assess` metadata: `"Assess a product offer with Jev; advisory only."` vs the schema paragraph about not buying/publishing/alerting.

**Por que é um problema:**
If a Hermes surface shows the metadata string instead of (or in addition to) the schema, the "not authorization" wording is weaker. The model-facing schema is still the longer text, so this is documentation drift, not a logic break.

**Solução:**
Pass `description=schemas.JEV_EVALUATE["description"]` and `description=schemas.JEV_PRICE_ASSESS["description"]` (or a shared constant). Extend `test_registers_tools_skill_and_command` to compare those strings.

**Explicação da correção:**
One string per tool, so a wording change cannot drift between schema and registration.

**Status now:** `register()` passes `schemas.JEV_*["description"]`; `test_registers_tools_skill_and_command` compares metadata to schema text.

---

### BUG-018: Playbook frontmatter version is stale — **Resolved**

**Arquivo:** `skills/jev-playbook/SKILL.md`
**Linha(s):** 4

**O que acontece:**
The skill YAML `version` is `0.1.0`. `plugin.yaml` / `pyproject.toml` / `PLUGIN_VERSION` are `0.2.2`. `test_plugin_version_matches_manifest_and_pyproject` does not read the skill.

**Por que é um problema:**
Operators and agents that key cache/reload on skill version may keep an old playbook mentally even after plugin bumps. No runtime break.

**Solução:**
Set the skill `version` to `0.2.2` (or a dedicated skill version) and assert it in `test_plugin_version_matches_manifest_and_pyproject` if the versions are meant to move together.

**Explicação da correção:**
Version strings stop implying the playbook is still the first 0.1.0 draft.

**Status now:** skill `version: 0.2.2`; `test_plugin_version_matches_manifest_and_pyproject` asserts lockstep with `PLUGIN_VERSION`, `plugin.yaml`, and `pyproject.toml`.

---

## Observações Gerais

What is in good shape:

- Stdlib-only client; transport is injectable; tests stay offline.
- Missing `TYPESAFE_API_KEY` fails before `opener`.
- `_NoRedirect` plus `HTTPError` drain/close; 1 MiB response cap; `allow_nan=False`; `parse_constant` rejects NaN/Inf.
- `_parse_answers` requires exact question names, typed bodies, noul in `[0, 1]`, choice in criteria, score in `[0, len(rubric)-1]`.
- Plugin `ok` / `error` are not overwritten by vendor fields.
- Timeouts and `max_state_chars` are clamped; full POST JSON capped at 200_000 characters; at most 32 questions.
- `_PRICE_QUESTIONS["seller_risk"]` is worst-to-best (higher score = more trustworthy seller).
- Hermes `register()` does not pass `attach_local_files=True`; `test_hermes_guard_does_not_read_local_files` locks that.
- `/jev` empty / invalid JSON never calls the API. Hermes documents `fn(raw_args: str) -> str | None`, so `handle_jev` calling `.strip()` is in contract.
- Dual import (`from .client` vs `from client`) is required for pytest loading the hyphenated directory.

False positives from SCAN patterns (not filed):

- `except Exception` in `_call_jev` / `_tool_call_guard` (required fail-closed wrappers, not `pass`).
- Dict `["answers"]` after `_parse_answers` / a successful evaluate.
- Test fixtures `api_key="x"` / `"secret-value"`.
- `timeout_seconds` of NaN: current `max(1.0, min(value, 600.0))` maps NaN to 1.0 (order-dependent; `math.isfinite` would still be clearer).
- Codex `attach_local_files=True` helpers in `tools.py` — unused on the Hermes registration path; routing lives in `jev-for-codex`.
- Noul rejecting extra keys including `confidence` — matches TypeSafe (noul has no confidence field).

All recommended fixes from this snapshot (BUG-012 through BUG-018) are applied.

---

## Appendix — previous snapshot (19/09/2026)

Those findings targeted an older tree. Status in the current code:

| ID | Original severity | Status now |
|----|-------------------|------------|
| BUG-001 | ALTO — redirect + Bearer | **Resolved** (`_NoRedirect`) |
| BUG-002 | ALTO — unbounded response body | **Resolved** (`_read_limited`, 1 MiB) |
| BUG-003 | ALTO — inverted `seller_risk` rubric | **Resolved** (worst → best) |
| BUG-004 | MEDIO — `answers` dict not shape-checked | **Resolved** (`_validate_answer_body`) |
| BUG-005 | MEDIO — vendor JSON overwrote `ok` | **Resolved** (allowlisted envelope) |
| BUG-006 | MEDIO — `NaN` / `Infinity` in request JSON | **Resolved** (`allow_nan=False`) |
| BUG-007 | MEDIO — only `state` size-capped | **Resolved** (payload cap + 32 questions) |
| BUG-008 | MEDIO — extra question keys forwarded | **Resolved** (allowlist + `additionalProperties: false`) |
| BUG-009 | MEDIO — `HTTPError` not closed | **Resolved** (`_close_http_error`) |
| BUG-010 | BAIXO — choice criteria not stripped | **Resolved** |
| BUG-011 | BAIXO — `PLUGIN_VERSION` drift | **Resolved** (test vs YAML/TOML) |
| BUG-012 | ALTO — contradictory `allow` + reason | **Resolved** (`allow` + `no_issue` only) |
| BUG-013 | MEDIO — metadata discarded | **Resolved** (`_copy_validated_answer_metadata`) |
| BUG-014 | MEDIO — loose `model` / `usage` | **Resolved** (allowlist + bounds) |
| BUG-015 | MEDIO — hook timeout race | **Resolved** (25s hook cap) |
| BUG-016 | BAIXO — choice criteria collision | **Resolved** (unique-after-strip check) |
| BUG-017 | BAIXO — registration description drift | **Resolved** (schema description reused) |
| BUG-018 | BAIXO — skill version stale | **Resolved** (0.2.2 + test) |
