# Advisory routing

Routing is independent of the optional fail-closed tool guard. All features default
to `off`. Start with `observe`: decisions are logged without changing the model
context, tools, or results. `suggest` enables skill, profile, and recovery advice;
`filter` enables tool and search-result selection. Nothing grants tool permission,
changes a model, starts a subagent, or sends an alert.

Use the plugin's Hermes settings, not project `.env` files. `skills_catalog` and
`profiles_catalog` contain `id`, `description`, and optional `details`. IDs are
unique, start with a letter/digit, contain only letters, digits, `_.:/-`, and are
at most 128 characters. Skill catalogs hold at most 255 entries; profile catalogs
hold at most 254, reserving the `keep` alternative. This follows the
[TypeSafe Choice limit](https://docs.typesafe.ai/primitives/choice) of 255 options.
Descriptions/details are bounded to 4096/8000 characters. Supply only available skills/profiles.
The plugin never discovers or opens skill files. Reload after configuration changes.

`routing_budget_seconds` defaults to 25 (clamped 1–25), shared across evaluations
in a turn. `routing_threshold` defaults to 0.7 (clamped 0–1); both must be finite
numbers. `essential_tools` lists exact tool names that filtering must keep.
Empty catalogs produce a diagnostic without an API call. Oversized request/state
payloads are rejected, never silently shortened to obtain a decision.

Turn state requires session/task/turn IDs and a textual user request. It is bounded
to 128 turns, 128 cached decisions/events per turn, and 15 minutes idle time.
At capacity or during a concurrent callback for the same turn, routing is skipped.
Failures are cached too. Logs use `jev.routing`, hashed correlation IDs, decisions,
mode, duration, API usage and error codes; no request or provider error text.
Hermes owns log retention. No filesystem logger is installed by the plugin.

`pre_llm_call` ranks the configured skill descriptions, then examines up to three
candidates' details and can reject all. It requires a complete probability map;
ties use the skill ID. The shared state includes the skill descriptions so the independent applicability
question has the catalog it needs. See [TypeSafe question isolation](https://docs.typesafe.ai/introduction).
Profiles choose one configured entry or keep the current
profile. Suggestions never load skills or switch profiles automatically. The
original Hermes skill index stays available; no token-saving claim is made.

`llm_request` supports Chat Completions and Responses function schemas. It retains
native/unknown tools, `skills_list`, `skill_view`, configured essential tools and
forced `tool_choice`. It does not discover, install, or enable MCPs. Tool relevance
is recomputed when schemas change and cached otherwise, including failures. An
empty or incomplete selection preserves all tools. At most 512 schemas are
classified, in batches of 32 questions.

`transform_tool_result` supports successful `web_search` (`data.web`) and
`session_search` (`mode: discover`, `results`) JSON lists of at most 128 items.
Selected items retain their complete contents, order and citations. `jev_filter`
records original/omitted counts; session-search `count` reflects the selected list.
Unknown formats, errors, incomplete evaluations, and empty selections pass through.

Recovery counts three consecutive errors per tool per turn, resets on success,
ignores blocked/cancelled calls and deduplicates `tool_call_id`. It suggests at most
once per tool/turn: another source, missing information, or main-model review.
Success clears pending recovery advice; later errors in the same turn do not
reactivate an old suggestion or start a second assessment for that tool.
Advice enters the next supported provider request, never an error body. The plugin
does not retry tools, change arguments, or bypass permissions.

## Configuration example

Merge these settings into the existing Hermes configuration under
`plugins.entries.jev-plugin-for-hermes.settings`, preserving other plugin entries:

```yaml
skills_mode: observe
tools_mode: off
results_mode: off
profiles_mode: off
recovery_mode: off
pre_tool_guard_enabled: false
routing_budget_seconds: 25
routing_threshold: 0.7
skills_catalog:
  - id: functional-analysis
    description: Analyze requirements and write acceptance criteria.
    details: Clarify actors, rules, constraints, and testable acceptance criteria.
  - id: research
    description: Research a question using external sources.
    details: Compare evidence, retain citations, and identify uncertainty.
  - id: godot-development
    description: Implement or debug a Godot game.
    details: Work with scenes, nodes, scripts, and reproducible game behavior.
profiles_catalog:
  - id: Analista
    description: Turn a request into a functional specification.
  - id: Chato
    description: Review a proposal for unsupported assumptions and tradeoffs.
  - id: Testador
    description: Verify a delivery against supplied acceptance criteria.
  - id: Dono
    description: Coordinate work that requires multiple stages.
essential_tools: []
```

Replace example IDs with the exact available skill/profile IDs. Reload the plugin
after changes. Enable INFO logging for `jev.routing` in the host to inspect
observation records. `observe` calls TypeSafe and consumes API usage even though
it leaves Hermes inputs unchanged. Start with skills only; compare suggestions
and abstentions against representative requests before changing to `suggest`.
Other modes can be enabled independently. No catalog is discovered automatically.

## External cron gate

`cron_gate.assess_gate` receives a target string and up to 32 normalized offers:
`{"target":"GPU model","offers":[{"id":"offer-123","eligible":true,"offer":{"title":"GPU"}}]}`.
IDs must be unique; input is bounded to 200000 characters. The collector owns
identity, price with shipping, freshness, deduplication, and seller policy.
`eligible` is trusted collector policy, not a claim from a listing.
Listing evidence is credential-redacted without truncating text or nested fields.
If the resulting state exceeds the client limit, assessment fails and the
eligible offer wakes Hermes for review instead of being judged on partial data.

The unchanged price rubric supplies recommendations. In `active`, `alert` and
`manual_review` wake Hermes; `ignore` and `record` do not. Assessment failures for
eligible offers wake for investigation. Invalid input/no eligible offers never
wake. Default `observe` wakes eligible offers while reporting `wouldWakeAgent`;
`off` skips Jev. Waking does not authorize buying or alerting.

Invoke `python /absolute/path/to/jev-plugin-for-hermes/cron_gate.py --mode observe`
with JSON on stdin. `--model` can pin a model; the CLI uses the default HTTPS
endpoint and `TYPESAFE_API_KEY` from the operator's environment. Python embedders
may supply custom `PluginSettings` to `assess_gate`.

For cron, create an operator-owned shell wrapper in `$HERMES_HOME/scripts/` that
executes this command with stdin redirected from the collector's normalized JSON.
Set that wrapper as the existing job's `script`. The plugin does not install the
wrapper or create jobs; file access belongs to the external wrapper/collector.
Preserve original evidence in the collector's store for investigating returned IDs.

Stdout ends with valid JSON; diagnostic codes go to stderr. Invalid input exits
zero so Hermes honors `wakeAgent: false`. Wrapper failures/process kills remain
outside the adapter's control. Eligible offers share a 25-second gate budget;
budget exhaustion wakes for review.

Network timeouts use the remaining routing budget and late answers are discarded.
Stdlib socket timeouts do not guarantee hard wall-clock cancellation. No retries
are made. Only time spent evaluating Jev consumes the interactive turn budget.

## Verification

Offline tests cover skill ranking with 182 alternatives, abstention, turn caching,
tool preservation, result order and citations, recovery deduplication and success
resets, and cron eligibility, recommendations, invalid input, and exhausted budgets.
Run `python -m pytest` and `python -m ruff check .` in the development environment.
These checks validate local behavior with simulated responses; they do not measure
selection quality or validate the live TypeSafe contract.

`hermes plugins doctor /path/to/plugin --ci` checks loading without making a Jev
API call. With routing disabled, it can warn that optional declared hooks were
not registered; this is expected because registration follows enabled modes.
Offline registration tests also cover enabled modes and a Hermes context without
middleware support.
