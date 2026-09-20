# Bug Report — refatoração in-place

> Data: 19/09/2026 | Stack: Python 3.11+ Hermes standalone plugin
> Modo: **quick** (arquivos alterados nesta refatoração)

## Sumário

| Severidade | Quantidade |
|------------|------------|
| CRITICO    | 0          |
| ALTO       | 0          |
| MEDIO      | 0          |
| BAIXO      | 0          |
| **Total**  | **0**      |

**Veredicto:** APROVADO

## Escopo

`client.py`, `tools.py`, `__init__.py`, `pyproject.toml`, testes e documentação.

## Observações

- Comportamento preservado: payloads HTTP, códigos de erro, schemas e `_PRICE_QUESTIONS`.
- `make_handlers` reutiliza um `JevClient` por registro; a chave continua resolvida por request.
- 33 testes offline passando; `hermes plugins doctor --ci` OK.
