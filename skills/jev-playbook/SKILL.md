---
name: jev-playbook
description: Use Jev for typed decisions in Hermes. Load this before routing or classifying evidence.
version: 0.3.0
---

# Jev Playbook for Hermes

Use the namespaced tool `jev-plugin-for-hermes:jev_evaluate` when a decision can be expressed as typed questions over supplied evidence. Use `jev-plugin-for-hermes:jev_price_assess` for an e-commerce offer. The registered `pre_tool_call` hook is a local no-op by default; operators must explicitly set `pre_tool_guard_enabled: true` when they want every Hermes tool invocation assessed by Jev before execution.

When enabled, the global hook is a fail-closed guard, not a replacement for Hermes policy. An `allow` result with reason code `no_issue` leaves normal execution and approvals unchanged; `review` or `deny` blocks the call before execution. Contradictory pairs such as `allow` + `destructive_change` are treated as malformed decisions and blocked. A failed assessment also blocks the call. Non-allow results include a bounded reason code such as `insufficient_context` or `deployment_or_release`; this is diagnostic context, not authorization. Keep the hook disabled for general-purpose sessions unless that latency and availability trade-off is intentional.

## Primitive selection

Optional routing starts in observation mode and is independent of the guard.
Skill/profile/recovery suggestions never override the user or authorize actions.
Tool and search-result filters require explicit operator configuration. Catalogs
come from settings, not filesystem discovery. Routing failures preserve Hermes'
normal behavior. See `docs/routing.md` in the plugin for configuration contracts.

The external cron gate may wake Hermes to investigate an eligible offer when Jev
fails. Waking is not permission to buy, publish or send an alert.

- `noul`: probability that one proposition is true. Example: “Does this listing exactly match the target product?”
- `choice`: one category from 1–255 named alternatives. Example: `ignore`, `record`, `manual_review`, or `alert`.
- `score`: a continuous position on an ordered rubric. Supply criteria from worst to best.
  In `jev_price_assess`, a higher `seller_risk` score means a more trustworthy
  seller, not more risk.

Put independent questions in one call. Keep the state factual and compact. Include the source evidence needed for the decision; do not ask Jev to retrieve data.

## Safe interpretation

Jev is an evaluator, not an authorization system. Treat confidence and probabilities as evidence for a policy, not as policy themselves.

For price monitoring, combine Jev's result with deterministic checks for:

1. exact product identity and variant;
2. numeric price, shipping, currency, and threshold;
3. freshness and duplicate URL/offer identity;
4. seller and marketplace allowlists;
5. human review when evidence is incomplete or confidence is intermediate.

Never let Jev alone buy, publish, delete, send money, or bypass a confirmation boundary. On timeout, HTTP error, malformed response, or missing `TYPESAFE_API_KEY`, fail closed and preserve the candidate for later review when appropriate.

## Model rollout

Use `jev-latest` while exploring. Once thresholds and fixtures are calibrated, set the plugin setting `default_model` to a pinned model such as `jev-1.13.0` and re-run the evaluation suite before changing production behavior.

The API key belongs in Hermes' secret environment as `TYPESAFE_API_KEY`; never place it in a prompt, source file, project config, or log.

## Manual command

The plugin also exposes `/jev` for a JSON payload:

```json
{"state":"...","questions":{"urgent":{"type":"noul","instructions":"Does this require urgent handling?"}}}
```

The command is for inspection and testing. It does not perform any side effect other than the Jev API request.
