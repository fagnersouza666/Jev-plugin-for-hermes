# Auditoria de Segurança — jev-plugin-for-hermes

> Sistema: jev-plugin-for-hermes (plugin Hermes nativo standalone)
> Data: 19/09/2026 | Versão: 0.1.0 |
> Stack: Python 3.11+ / Hermes Agent plugin (stdlib only)
> Baseado em: OWASP Top 10:2021, LGPD (Lei 13.709/2018), CWE

## Sumário de Segurança

- CRÍTICOS: 0
- ALTOS: 0
- MÉDIOS/BAIXOS: 4
- Pontos positivos: stdlib-only; fail-closed em config, transporte e handlers; redirect desabilitado; Bearer não repassado em redirect; bounds de request/response; allowlist de questions; handlers não vazam exception/body/key; envelope `ok` do plugin não sobrescrito pelo vendor; skill e schemas declaram “evidence, not authorization”; `.gitignore` cobre secrets; testes offline.
- Veredicto: **APROVADO COM RESSALVAS**

---

## Escopo e método

Auditoria **full-project** (modo audit) sobre:

- Código: [`client.py`](../client.py), [`tools.py`](../tools.py), [`schemas.py`](../schemas.py), [`__init__.py`](../__init__.py), [`plugin.yaml`](../plugin.yaml), [`tests/`](../tests/), [`skills/jev-playbook/SKILL.md`](../skills/jev-playbook/SKILL.md)
- Playbook OWASP (Python): SQLi, BAC, crypto, misconfig, CMDi, path traversal, auth, deser, logs, SSRF, upload, CSRF, LGPD, deps
- Grafo (codebase-memory): `list_projects`, `index_status`, `get_architecture`, `query_graph`, `search_graph`, `check_index_coverage`

**Limitação de cobertura:** index `ready` (144 nós), porém `_parse_answers` / `_post_json` não indexados como símbolos; achados de linha confirmados por leitura de fonte e testes.

**Superfície:** plugin in-process no agente Hermes. Único efeito colateral: um POST HTTPS para TypeSafe Jev. Não há servidor HTTP, SQL, upload, subprocess, MCP ou filesystem privilegiado.

---

## CRÍTICO — Bloqueia deploy

Nenhum achado crítico.

---

## ALTO — Corrigir antes da próxima release

Nenhum achado alto no tree atual.

> **Nota histórica:** [`docs/bug-report.md`](bug-report.md) lista 3 ALTOs (redirect+Bearer, body ilimitado, rubrica invertida) contra uma versão anterior. BUG-001–009 estão **mitigados** no código atual (ver seção “Resoluções”).

---

## MÉDIO/BAIXO — Backlog de segurança

### SEC-001: Validação semântica incompleta de `answers` (CORRIGIDO nesta auditoria)

**Localização:** [`client.py`](../client.py) — `_parse_answers`, `_validate_answer_body`

**Descrição:** Antes desta auditoria, `_parse_answers` validava apenas chaves e `type`, permitindo `{ok: true}` com corpos incompletos (`type` sem `noul`/`choice`/`score`), valores fora de faixa ou `choice` fora dos critérios. Isso quebrava o contrato fail-closed documentado no README.

**Referência:** OWASP A05:2021 — Security Misconfiguration | CWE-20 (Improper Input Validation)

**Evidência (estado anterior):**

```python
for name, body in answers.items():
    if not isinstance(body, dict) or body.get("type") != expected[name]["type"]:
        raise JevProtocolError("Jev API response did not contain an answers object")
return decoded
```

**Impacto:** Pipeline downstream (ex.: price-monitor) poderia tratar resposta incompleta como avaliação válida.

**Correção aplicada:** `_validate_answer_body` exige `noul` ∈ [0,1] finito, `choice` ∈ critérios, `score` ∈ [0, len(rubric)-1], allowlist estrita de chaves por tipo; resposta sanitizada sem campos vendor extras.

**Se não corrigido:** API hostil ou resposta malformada induz decisão de política errada em consumidor que confia só em `ok: true`.

---

### SEC-002: `NaN`/`Infinity` em JSON de resposta (CORRIGIDO nesta auditoria)

**Localização:** [`client.py`](../client.py) — `_parse_answers`; [`tools.py`](../tools.py) — `_json`

**Descrição:** `json.loads` aceitava `NaN`/`Infinity`; `_json` serializava sem `allow_nan=False`, podendo gerar JSON inválido no loop do agente.

**Referência:** OWASP A05:2021 — Security Misconfiguration | CWE-704 (Incorrect Type Conversion)

**Evidência:**

```python
# client.py (antes)
decoded = json.loads(raw.decode("utf-8"))

# tools.py (antes)
return json.dumps(payload, ensure_ascii=False, sort_keys=True)
```

**Correção aplicada:** `parse_constant` rejeita NaN/Inf; `_json` usa `allow_nan=False`; `usage` validado como JSON-compatível.

---

### SEC-003: Repasse de campos vendor não allowlisted (CORRIGIDO nesta auditoria)

**Localização:** [`client.py`](../client.py) — `_parse_answers`; [`tools.py`](../tools.py) — `_call_jev`

**Descrição:** Resposta completa do vendor (`ok`, `error`, chaves extras) era retornada de `evaluate()`. Handlers já filtravam parcialmente, mas o client não garantia payload mínimo.

**Referência:** OWASP A04:2021 — Insecure Design | CWE-915 (Improperly Controlled Modification of Dynamically-Determined Object Attributes)

**Correção aplicada:** `_parse_answers` retorna só `answers`, `model` (string), `usage` (dict JSON-safe). Testes confirmam stripping de `ok`/`error` vendor.

---

### SEC-004: `api_url` configurável para destinos privados (aceito — documentado)

**Localização:** [`client.py`](../client.py) — `_validate_endpoint` (linhas 212–222); [`README.md`](../README.md); [`AGENTS.md`](../AGENTS.md)

**Descrição:** Operador pode apontar `api_url` para IPs privados ou metadata cloud via HTTPS. Não é SSRF via argumentos de tool — apenas config explícita do operador.

**Referência:** OWASP A10:2021 — SSRF | CWE-918

**Evidência:**

```python
if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise JevConfigurationError("api_url must use HTTPS except for local test endpoints")
```

**Impacto:** Com `TYPESAFE_API_KEY` e `api_url` maliciosos, o operador expõe a própria chave ao destino configurado.

**Correção aplicada:** README (Plugin settings + Security boundary), AGENTS.md invariante 7, e [`docs/development.md`](development.md) documentam `api_url` como **trust boundary do operador**. Comportamento de `_validate_endpoint` inalterado (sem bloqueio RFC1918/link-local).

**Backlog opcional:** bloquear RFC1918/link-local em builds de produção, se exigido pelo consumidor.

---

### SEC-005: Ausência de pipeline CI de segurança (CORRIGIDO)

**Localização:** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

**Descrição:** Não havia gate automatizado de `pip-audit`, gitleaks ou scan de imagem. Runtime sem dependências reduz A06; dev deps (`pytest`, `ruff`) não eram auditadas automaticamente.

**Referência:** OWASP A06:2021 — Vulnerable and Outdated Components

**Correção aplicada:** GitHub Actions com dois jobs — `quality` (ruff + pytest) e `security` (pip-audit 2.10.1 sobre extras dev + gitleaks CLI). Documentado em README e `docs/development.md`.

**Comandos locais equivalentes:**

```bash
pip install -e '.[dev]' pip-audit==2.10.1 && python -m pip_audit
gitleaks detect --source . --verbose --redact
python -m pytest -q
python -m ruff check .
```

---

## Verificação de Componentes Vulneráveis (PLAY-15)

| Ecossistema | Runtime | Dev | Status |
|-------------|---------|-----|--------|
| Python plugin | **0 deps** (`pyproject.toml`) | pytest, ruff | Runtime N/A; dev auditado em CI (`pip-audit`) |

**Conclusão A06:** risco baixo em produção (stdlib only). Auditar dev extras periodicamente.

---

## Conformidade LGPD

Plugin não persiste dados; `state`/`offer` podem conter PII comercial transitória enviada ao TypeSafe.

| Requisito LGPD | Status | Detalhes |
|----------------|--------|----------|
| Base legal documentada (Art. 7) | NÃO VERIFICÁVEL | Responsabilidade do consumidor (price-monitor) |
| Minimização de dados (Art. 6, III) | PARCIAL | Plugin limita tamanho (`max_state_chars`, payload 200k); consumidor deve enviar mínimo necessário |
| Mascaramento de CPF em exibição | N/A | Plugin não exibe dados |
| Mascaramento de CPF em logs | CONFORME | Plugin não loga state/key/body |
| Log de acesso a dados pessoais | N/A | Sem persistência local |
| Direito de acesso/exportação (Art. 18) | N/A | Sem armazenamento |
| Direito de eliminação (Art. 18) | N/A | Sem armazenamento |
| Direito de portabilidade (Art. 18) | N/A | Sem armazenamento |
| Criptografia em repouso | N/A | Sem persistência |
| Criptografia em trânsito | CONFORME | HTTPS obrigatório (exceto loopback dev) |
| Plano de resposta a incidentes | NÃO VERIFICÁVEL | Fora do escopo do plugin |

**Se não corrigido:** Vazamento de PII em `state` depende do operador/consumidor e do DPA com TypeSafe, não do plugin em si.

---

## Threat Modeling — Superfície de Ataque

### Atores de Ameaça

| Ator | Motivação | Vetores de Ataque | Consequência se bem-sucedido |
|------|-----------|-------------------|------------------------------|
| Atacante externo | Roubar API key | Redirect/open redirect no endpoint (mitigado), phishing de config | Uso indevido da conta TypeSafe |
| Agente/modelo malicioso | Injetar evidência em `state` | Prompt injection via tool args | Avaliação Jev enviesada; não compra direta |
| Operador misconfig | Apontar `api_url` errado | Config Hermes | Key enviada a host errado |
| Supply chain | Comprometer repo | Commit de secret (mitigado por .gitignore) | Exposição de key no histórico |

### Mapa de Superfície

```
Ponto de Entrada              | Auth        | Rate Limit | Input Validation | Side Effects
------------------------------|-------------|------------|------------------|-------------
jev_evaluate (tool)           | Bearer env  | N/A (Hermes)| Sim (bounds)    | HTTPS POST
jev_price_assess (tool)       | Bearer env  | N/A        | Sim              | HTTPS POST
/jev (command)                | Bearer env  | N/A        | JSON parse       | HTTPS POST
plugin register               | N/A         | N/A        | path fixo skill  | Nenhum
```

### Cenários de ataque combinados

**Cenário 1 — Redirect + Bearer (histórico, mitigado):**
Endpoint comprometido → redirect com Authorization → exfiltração de key. **Mitigação:** `_NoRedirect`, testes em `test_no_redirect_handler_rejects_redirects`.

**Cenário 2 — Resposta malformada + política automática:**
API retorna `type` sem valor → antes `ok: true` → alerta indevido. **Mitigação:** SEC-001 corrigido.

**Cenário 3 — Prompt injection em state:**
Modelo inclui instrução adversarial em `state` → Jev retorna recomendação alta → consumidor age sem gates determinísticos. **Mitigação documental:** skill + schemas; **não** mitigação técnica no plugin.

---

## Resoluções — bug-report histórico

| ID | Severidade original | Status no tree atual |
|----|---------------------|----------------------|
| BUG-001 | ALTO — redirect + Bearer | **Resolvido** — `_NoRedirect` |
| BUG-002 | ALTO — body ilimitado | **Resolvido** — `_read_limited` 1 MiB |
| BUG-003 | ALTO — rubrica seller_risk | **Resolvido** — critérios worst→best |
| BUG-004–009 | MÉDIO/BAIXO | **Resolvido** — ver commits/tests |

Consulte nota no topo de [`docs/bug-report.md`](bug-report.md).

---

## Pontos positivos (controles verificados)

| Controle | Evidência |
|----------|-----------|
| Stdlib only, sem import-time network | `AGENTS.md`, `client.py` |
| Redirect desabilitado | `client.py:232-238`, `tests/test_client.py` |
| Endpoint validation (HTTPS, sem credenciais/query/fragment) | `client.py:211-221` |
| Request bounds (state, payload, 32 questions, no NaN) | `client.py:117-207` |
| Response bounds (1 MiB) | `client.py:241-245` |
| Secret fail-closed antes de transport | `client.py:345-349` |
| Handlers não vazam exception/body | `tools.py:112-116`, `tests/test_tools.py` |
| Envelope plugin preservado | `tests/test_tools.py` |
| TYPESAFE_API_KEY secret in manifest | `plugin.yaml:14-18` |
| Testes offline | `tests/` |

---

## Recomendações prioritárias

1. **Manter** validação semântica de answers (implementada) com testes de regressão.
2. **Sincronizar** `docs/bug-report.md` com status resolvido (nota adicionada).
3. **Manter** CI (pytest, ruff, pip-audit, gitleaks) e documentação de `api_url` como trust boundary.
4. **Consumidores:** nunca tratar Jev como autorização; gates determinísticos antes de alert/buy.

---

## Veredicto final

**APROVADO COM RESSALVAS** — 0 crítico, 0 alto. Lacunas fail-closed em validação de resposta (SEC-001–003) **corrigidas** nesta auditoria. SEC-004 documentado; SEC-005 mitigado com CI. Riscos residuais são operacionais (misconfig de `api_url`, prompt injection via `state`) e dependem do consumidor Hermes.
