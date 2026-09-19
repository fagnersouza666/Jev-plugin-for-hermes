---
name: jev-playbook
description: Use Jev for typed decisions in Hermes. Load this before routing or classifying evidence.
version: 0.1.0
---

# Jev Playbook for Hermes

Use the namespaced tool `jev-plugin-for-hermes:jev_evaluate` when a decision can be expressed as typed questions over supplied evidence. Use `jev-plugin-for-hermes:jev_price_assess` for an e-commerce offer.

## Primitive selection

- `noul`: probability that one proposition is true. Example: “Does this listing exactly match the target product?”
- `choice`: one category from a named set. Example: `ignore`, `record`, `manual_review`, or `alert`.
- `score`: a continuous position on an ordered rubric. Supply criteria from worst to best.

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
