# Bug Report — jev-plugin-for-hermes

> Date: 19/09/2026 | Stack: Python 3.11+ Hermes standalone plugin (stdlib `urllib`, no runtime deps)
> Mode: **full** (snapshot histórico)
> Files analyzed: 8 Python modules (`client.py`, `tools.py`, `__init__.py`, `schemas.py`, `tests/conftest.py`, `tests/test_client.py`, `tests/test_tools.py`, `tests/test_registration.py`) plus `plugin.yaml` and `skills/jev-playbook/SKILL.md`

Graph (codebase-memory): `list_projects`, `index_status`, `get_architecture`, `search_graph` (Function/Class/Method), `trace_path(evaluate)`, `check_index_coverage`. Index was `ready` with no parse gaps, but several working-tree files were `metadata_changed` and `_post_json` was not in the graph — line-level findings below come from source reads, not from the stale symbol table.

---

## Status de resolução (atualizado 19/09/2026)

Este documento descreve achados contra uma **versão anterior** do código. No tree atual, **BUG-001 through BUG-009 estão mitigados** em `client.py`, `tools.py`, e `schemas.py`, com testes correspondentes em `tests/test_client.py` e `tests/test_tools.py`.

| ID | Severidade original | Status atual |
|----|---------------------|--------------|
| BUG-001 | ALTO — redirect + Bearer | **Resolvido** (`_NoRedirect`) |
| BUG-002 | ALTO — body ilimitado | **Resolvido** (`_read_limited`, 1 MiB) |
| BUG-003 | ALTO — rubrica seller_risk | **Resolvido** (critérios worst→best) |
| BUG-004–009 | MÉDIO/BAIXO | **Resolvido** (ver código e testes) |

Para triagem de segurança atual, use [`docs/relatorio-seguranca.md`](relatorio-seguranca.md) (auditoria OWASP/LGPD, veredicto **APROVADO COM RESSALVAS**).

---

## Sumário (snapshot histórico)

| Severidade | Quantidade |
|------------|------------|
| CRITICO    | 0          |
| ALTO       | 3          |
| MEDIO      | 5          |
| BAIXO      | 2          |
| **Total**  | **10**     |

**Veredicto (snapshot):** ATENÇÃO

The happy path (valid arguments, trusted TypeSafe `200` JSON) does not crash. Three ALTO issues can leak the API key, exhaust memory, or invert seller-risk scores in the price-monitor pipeline.

---

## CRITICO

Nenhum. No null-deref, SQL injection, or always-on production crash in the main tool loop. Handlers wrap unexpected exceptions as `{ok: false, error.code: internal_error}`.

---

## ALTO

### BUG-001: `urlopen` follows redirects and forwards `Authorization`

**Arquivo:** `client.py`
**Linha(s):** 217-244

**O que acontece:**
`_post_json` uses the default `urllib.request.urlopen` opener. CPython's `HTTPRedirectHandler.redirect_request` copies request headers except `content-length` / `content-type`. For POST, status `301`, `302`, and `303` are followed (typically as GET) **including `Authorization: Bearer …`**. `_validate_endpoint` is not re-run on `Location`, so a redirect can target cleartext `http://`, another host, or link-local addresses that the plugin would reject as `api_url`.

**Por que é um problema:**
If the configured HTTPS endpoint (compromised vendor, mis-set `api_url`, or an open redirect) returns `Location: https://attacker.example/steal`, the TypeSafe key is sent to the attacker. `Location: http://127.0.0.1:…` or cloud metadata URLs also bypass the plugin's "HTTPS except loopback" rule. This is the only secret the plugin holds.

**Código problemático:**

```python
http_request = Request(
    endpoint,
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"jev-plugin-for-hermes/{PLUGIN_VERSION}",
    },
)
try:
    with opener(http_request, timeout=timeout) as response:
        status = int(getattr(response, "status", response.getcode()))
        raw = response.read()
```

**Solução:**

```python
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open


# JevClient.__init__:
self._opener = opener or _default_opener()
```

Treat `301`/`302`/`303`/`307`/`308` as `JevHTTPError` (already mapped). Do not follow `Location`.

**Explicação da correção:**
A JSON API client should talk to one validated URL. Disabling redirects preserves `_validate_endpoint` and keeps the Bearer token on that URL only.

---

### BUG-002: Response body is read with no size limit

**Arquivo:** `client.py`
**Linha(s):** 237-239, 247-256

**O que acontece:**
`response.read()` pulls the entire body into memory, then `raw.decode("utf-8")` + `json.loads` allocate again. Request `state` is clamped (`max_state_chars`, default 20_000, max 200_000), but the response is unbounded. Timeouts (1–600s) do not cap bytes.

**Por que é um problema:**
A `200` with a multi-gigabyte body (buggy gateway, wrong `api_url`, or a redirect target if BUG-001 remains) can OOM the Hermes process inside the tool call. Fail-closed elsewhere does not help if the process is killed first.

**Código problemático:**

```python
with opener(http_request, timeout=timeout) as response:
    status = int(getattr(response, "status", response.getcode()))
    raw = response.read()
```

**Solução:**

```python
_MAX_RESPONSE_BYTES = 1_000_000  # keep in sync with a documented setting if exposed

def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise JevProtocolError("Jev API response exceeded the size limit")
    return data
```

Use the same limit in `_parse_answers`. Reject oversize before `json.loads`.

**Explicação da correção:**
A hard cap matches the existing `max_state_chars` philosophy: bounded plugin state, fail closed, no partial answers.

---

### BUG-003: `seller_risk` score rubric is inverted relative to `deal_quality` and the documented contract

**Arquivo:** `tools.py`
**Linha(s):** 42-59

**O que acontece:**
Jev `score` criteria are documented as **worst → best** (`AGENTS.md`, `README.md`, `skills/jev-playbook/SKILL.md`). `deal_quality` follows that (invalid → exceptional). `seller_risk` lists the **safest** seller first and the **riskiest** last.

**Por que é um problema:**
The first intended consumer is a price-monitor pipeline. If it treats every score as "higher is better" (same as `deal_quality`), a high `seller_risk` looks like a good seller when the last rubric line is "High risk signals…". If it treats `seller_risk` as "higher means more risk", that contradicts the playbook's worst-to-best rule. Either way, the two scores in the same `_PRICE_QUESTIONS` payload do not share polarity. Changing this set is product policy.

**Código problemático:**

```python
"seller_risk": {
    "type": "score",
    "instructions": "How risky is the seller or listing for a purchase decision?",
    "criteria": [
        "No meaningful risk signals and strong evidence",
        "Some uncertainty or moderate risk signals",
        "High risk signals, weak evidence, or suspicious seller",
    ],
},
"deal_quality": {
    "type": "score",
    "instructions": "How attractive is the offer after considering price, shipping, condition, and fit?",
    "criteria": [
        "Invalid or unattractive offer",
        "Usable but weak offer",
        "Good offer",
        "Exceptional offer",
    ],
},
```

**Solução:**

```python
"seller_risk": {
    "type": "score",
    "instructions": "How trustworthy is the seller or listing for a purchase decision?",
    "criteria": [
        "High risk signals, weak evidence, or suspicious seller",
        "Some uncertainty or moderate risk signals",
        "No meaningful risk signals and strong evidence",
    ],
},
```

Re-calibrate any downstream thresholds that assumed increasing risk = increasing score. Add a unit test that locks the ordered lists.

**Explicação da correção:**
Both scores then mean "higher is a better purchase signal". The playbook's worst-to-best rule holds without a special case for risk.

---

## MEDIO

### BUG-004: `answers` is accepted if it is any dict — inner shape is not checked

**Arquivo:** `client.py`
**Linha(s):** 247-256

**O que acontece:**
Fail-closed is implemented as "HTTP 2xx + JSON object + `answers` is a dict". Missing question names, `answers: {}`, `answers: {"q": null}`, or `noul: "banana"` are returned to the model as `{ok: true, ...}`.

**Por que é um problema:**
`AGENTS.md` says malformed responses must not proceed as a partial answer. A caller that does `result["answers"]["exact_match"]["noul"]` can throw in the agent, or a policy check can treat empty answers as a successful evaluation.

**Código problemático:**

```python
if not isinstance(decoded, dict) or not isinstance(decoded.get("answers"), dict):
    raise JevProtocolError("Jev API response did not contain an answers object")
return decoded
```

**Solução:**

```python
def _parse_answers(raw: bytes, status: int, expected: Mapping[str, Any]) -> dict[str, Any]:
    # ... decode as today ...
    answers = decoded.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(expected):
        raise JevProtocolError("Jev API response did not contain an answers object")
    for name, body in answers.items():
        if not isinstance(body, dict) or body.get("type") != expected[name]["type"]:
            raise JevProtocolError("Jev API response did not contain an answers object")
    return decoded
```

Pass `request.questions` from `evaluate`. Keep messages generic (no raw body).

**Explicação da correção:**
Success then means "every asked question has a typed answer object", which matches the plugin's fail-closed invariant.

---

### BUG-005: Vendor JSON is merged on top of `{ok: true}` in `jev_evaluate`

**Arquivo:** `tools.py`
**Linha(s):** 102-106

**O que acontece:**
`_call_jev` does `{"ok": True, **response}` when `result_key` is omitted (`jev_evaluate` / `/jev`). Keys from the API overwrite the envelope. `jev_price_assess` nests under `assessment` and is safe.

**Por que é um problema:**
If the API adds `ok`, `error`, or another reserved field, the tool result no longer matches `{ok: true, answers: ...}`. A vendor `ok: false` with a valid `answers` object would look like a local failure without `error.code`.

**Código problemático:**

```python
response = client.evaluate(state=state, questions=questions, model=model)
if result_key is None:
    return _json({"ok": True, **response})
return _json({"ok": True, result_key: response})
```

**Solução:**

```python
response = client.evaluate(state=state, questions=questions, model=model)
envelope = {"ok": True, "answers": response["answers"]}
if "model" in response:
    envelope["model"] = response["model"]
if "usage" in response:
    envelope["usage"] = response["usage"]
if result_key is None:
    return _json(envelope)
return _json({"ok": True, result_key: envelope})
```

**Explicação da correção:**
The plugin owns `ok` / `error`. Vendor fields are copied by allowlist, same idea as not echoing HTTP bodies.

---

### BUG-006: `json.dumps` serializes `NaN` / `Infinity` into the request body

**Arquivo:** `client.py`
**Linha(s):** 116-120, 312

**O que acontece:**
Python's `json.dumps` defaults to `allow_nan=True`. A state object such as `{"price": float("nan")}` becomes the token `NaN`, which is not JSON. `_serialized_state` only catches `TypeError` / `ValueError` from non-serializable types, so this is not turned into `JevValidationError`.

**Por que é um problema:**
The API may return `400` (`JevHTTPError`) instead of a local validation error, or a non-Python parser may reject the body. The plugin claims state must be JSON-compatible.

**Código problemático:**

```python
text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

**Solução:**

```python
text = json.dumps(
    state,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
)
```

Use the same flags when encoding `request.as_payload()`.

**Explicação da correção:**
`allow_nan=False` raises `ValueError`, already mapped to `JevValidationError` ("state must contain only JSON-compatible values").

---

### BUG-007: Only `state` is size-capped; questions, criteria, and `model` are not

**Arquivo:** `client.py`
**Linha(s):** 113-128, 177-193

**O que acontece:**
`max_state_chars` applies to serialized evidence. `questions` can contain many keys, long `instructions`, large `criteria` maps/lists, and a huge `model` string. That payload is POSTed in full.

**Por que é um problema:**
A jailbroken or looping tool call can send a very large body, burn TypeSafe quota, and sit on the timeout (up to 600s). This is weaker than BUG-002 (local OOM from the response) but still unbounded egress.

**Código problemático:**

```python
return JevRequest(
    state=_serialized_state(state, max_state_chars),
    model=model.strip(),
    questions=normalized,
)
```

**Solução:**
After building `JevRequest`, measure `len(json.dumps(request.as_payload(), ...))` and reject above a clamp (for example the same `max_state_chars` applied to the full body, or a dedicated `max_request_chars`). Optionally cap `len(questions)` (e.g. 32).

**Explicação da correção:**
One number already exists for "how much we are willing to send". Applying it to the whole JSON body closes the gap.

---

### BUG-008: Question objects are copied with unknown keys still attached

**Arquivo:** `client.py`
**Linha(s):** 152-174
**Arquivo:** `schemas.py`
**Linha(s):** 12-27 (`additionalProperties: True`)

**O que acontece:**
`_validate_question` does `question = dict(raw)` and then overwrites `type` / `instructions` / `criteria`. Extra keys (`temperature`, nested objects, leftover `criteria` on `noul`) are forwarded to TypeSafe.

**Por que é um problema:**
The plugin is supposed to send a strict contract. Extra fields can change vendor behavior or trigger opaque HTTP errors. The JSON schema tells the model that extra properties are allowed.

**Código problemático:**

```python
question = dict(raw)
kind = question.get("type")
# ...
return question
```

**Solução:**

```python
out: dict[str, Any] = {
    "type": kind.lower(),
    "instructions": instructions.strip(),
}
if out["type"] == "choice":
    out["criteria"] = _validate_choice_criteria(question, name)
elif out["type"] == "score":
    out["criteria"] = _validate_score_criteria(question, name)
return out
```

Set `"additionalProperties": False` on `_QUESTION` in `schemas.py`.

**Explicação da correção:**
Allowlisting matches noul/choice/score as documented and keeps the wire payload deterministic.

---

### BUG-009: `HTTPError` from `urlopen` is not closed

**Arquivo:** `client.py`
**Linha(s):** 236-243

**O que acontece:**
On HTTP 4xx/5xx, `urlopen` raises `HTTPError` before the `with` block starts. `HTTPError` is a file-like response. The handler maps `exc.code` and does not `close()` / drain the body.

**Por que é um problema:**
Each failed call can leave a socket until GC. One tool call is small; a retry loop or a busy agent session leaks descriptors. The body is discarded (good for secret hygiene) but the resource is not.

**Código problemático:**

```python
except HTTPError as exc:
    raise JevHTTPError(int(exc.code)) from exc
```

**Solução:**

```python
except HTTPError as exc:
    try:
        status = int(exc.code)
        exc.read(1)  # discard; do not include body in JevHTTPError
    finally:
        exc.close()
    raise JevHTTPError(status) from exc
```

**Explicação da correção:**
Same fail-closed HTTP mapping, with the socket released immediately.

---

## BAIXO

### BUG-010: Choice criteria keys and descriptions are not stripped

**Arquivo:** `client.py`
**Linha(s):** 131-140 vs 143-149

**O que acontece:**
Score labels are `.strip()`ped. Choice keys/values are only checked for non-empty `strip()`, then stored as original strings (`" a "` stays `" a "`).

**Por que é um problema:**
A model-generated choice name with padding may not match what downstream code expects (`"new"` vs `"new "`). Low impact because the usual caller is the LLM, not a strict enum.

**Solução:**
Store `{k.strip(): v.strip() for k, v in criteria.items()}` after the existing emptiness checks.

---

### BUG-011: `PLUGIN_VERSION` is a second copy of `plugin.yaml` / `pyproject.toml`

**Arquivo:** `client.py`
**Linha(s):** 22

**O que acontece:**
`User-Agent: jev-plugin-for-hermes/0.1.0` is hardcoded. Manifest and packaging versions are separate strings.

**Por que é um problema:**
A release that bumps `plugin.yaml` but not `PLUGIN_VERSION` sends a stale UA. Not a functional break.

**Solução:**
Single constant imported by tests that assert `PLUGIN_VERSION ==` the YAML/TOML version, or generate UA from `plugin.yaml` at register time without a network read beyond the local file.

---

## Observações Gerais

What is in good shape:

- Stdlib-only client; no vendor SDK; transport is injectable and tests stay offline.
- Missing `TYPESAFE_API_KEY` fails before `opener` (`test_missing_key_fails_before_transport`).
- `_validate_endpoint` rejects credentials, query, fragment, and non-loopback HTTP.
- Timeouts and `max_state_chars` are clamped.
- HTTP status and transport failures become typed errors; handlers do not echo exception text or response bodies (`test_*_internal_error_does_not_leak_exception_text`).
- `except Exception` in `_call_jev` is intentional (Hermes tool loop) and is not a silent `pass`.
- Dual import (`from .client` vs `from client`) is required for pytest loading the hyphenated directory.
- `/jev` empty / invalid JSON never calls the API. Hermes invokes `fn(raw_args: str)` and catches command exceptions in `cli.py`, so a `None` argument is not a realistic agent-loop break.
- `_PRICE_QUESTIONS` is copied inside `_validate_question`; the module dict is not mutated per request.
- `jev_price_assess` keeps the "advisory, not authorization" wording.

False positives from SCAN patterns (not filed):

- `except Exception` in `tools.py` (required fail-closed wrapper).
- `question["type"]` after an `isinstance` check.
- Test `api_key="x"` / `"secret-value"` fixtures (not production secrets).

Recommended fix order: BUG-001, BUG-002, BUG-003, then protocol tightening (004–008).
