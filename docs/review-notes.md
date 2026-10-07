# Review notes

This records the evaluation fixes and the v0.2 extension. The six core
scenarios and the scoring formula remain unchanged. The final comparison uses
one frozen source commit; development checks and earlier interrupted attempts
are kept separate from its results.

## Operational balances retain their precision

The displayed remaining quantity was rounded to four decimals. After buying
0.333333 units, the displayed remainder was 499.6667, which the simulator
rejected for exceeding a 500-unit target. Remaining budget could similarly
round upwards. The tools now return exact operational remainders while
keeping the original presentation of other display fields.

Why: an agent must be able to use the balance supplied by the environment
without being penalised by an interface rounding error. We fixed the
observation; we did not loosen the trading restrictions.

## Non-finite orders are invalid inputs

`NaN` comparisons could bypass the target and price-cap checks. The simulator
now rejects non-finite quantities and limit prices before changing any
state. These malformed inputs follow the existing invalid-quantity path;
they do not introduce a new scoring penalty.

Why: validation must run before economic constraints or execution. Native
JSON clients may reject these values too, but the simulator also accepts
Python calls and replayed action lists and must enforce its own boundary.

## Regression evidence

Tests cover fractional remainders, budget precision, atomic rejection of
non-finite input, cumulative budgets and the permanence of market closure.
The Inspect wiring test now compares execution shortfall and the actual
action log against TWAP, uses the default message limit and writes logs to a
test-specific temporary directory. A saturated score of 1 alone cannot
establish that two policies are equivalent.

The original six-scenario TWAP/dump table reproduces unchanged. CLI transport
tests use synthetic process output; they are not real-model results.

The full test suite passes with Inspect 0.3.276. Both CLI providers also pass
an integration test that runs scripted decisions through the real Inspect
loop, market tools and scorer, comparing the complete action log with TWAP.
The test checks that private sample metadata never reaches the transport and
that model-call details survive beyond Inspect's default five-call cutoff.
The earlier development smoke run `20261006T085315879123Z` completed successfully
for all four
requested models on 6 October 2026, using Inspect 0.3.276, Claude Code 2.1.290
and Codex CLI 0.160.1. Each evaluated `deep_calm` once without hitting the
message limit. These four smoke samples validate live integration; they are
excluded from the full core experiment and its aggregate results.

A live smoke attempt with Claude Code 2.1.290 identified an unsupported
`system/thinking_tokens` event. The parser had treated routine CLI telemetry
as unexpected execution activity. It now distinguishes documented telemetry
(including progress, thinking counters, timing, notifications, quota updates
and heartbeats) from actions. These events remain in the raw log; reported
token usage still comes from the final result. Tools, model fallbacks, hooks
and control requests remain rejected. The failed attempts are not model scores.

The later smoke attempt `20261006T084149231361Z` was rejected when Claude
emitted a native `tool_use` named `advance`. This records an attempted call,
not confirmed execution; the adapter did not map it to an Inspect action.
The prompt previously requested a "tool call" while prohibiting CLI tools,
which also obscured the required `StructuredOutput` formatting step. It now
labels benchmark schemas as `action_definitions` and explicitly separates
the external executor's actions from that formatting call. The parser still
rejects native benchmark calls. Failed attempts provide no benchmark scores;
the successful smoke run above followed this correction.

Codex CLI 0.160.1 rejected the obsolete `tools.view_image` configuration key;
the equivalent feature remains disabled through `features.view_image=false`.
Its JSON stream also represents configuration warnings as non-fatal
`item.error` records. The parser now validates those records according to the
official SDK contract, retaining them in raw responses. Fatal stream errors,
failed turns, model-reroute notices and native tool activity still reject the
request. Mixed model identifiers anywhere in either provider's response also
reject it; a final matching identifier cannot hide an earlier discrepancy.

Inspect traces are stored with the run logs and its optional live SQLite
buffer is disabled, keeping routine diagnostics with each experiment.
Inspect's control surface and the CLIs can still require their own writable
runtime directories. We use Inspect's native JSON log format
after observing a stall in this environment when loading `.eval` files.

## CLI track

The owner requested evaluation through existing CLI sessions. The added
providers keep the original Inspect loop and market tools while replacing
the model transport with a logged-in CLI invocation. The protocol documents
the resulting limitations, provenance and validation gates. No credentials
are extracted or stored in the project.

The design separates proposed actions from their execution: a model proposes,
while deterministic code enforces limits and records outcomes. Operational
data and private source from other projects are not included in this repository.

## Generated-scenario extension

`_affordable_qty` previously applied its 0.1% budget margin whenever available
quantity was below the requested quantity, including some cases where liquidity
or a price cap was the binding constraint. The six core reference results were
not affected. The extension corrects this behaviour and adds focused regression
tests for budget, depth and cap boundaries. Full action lists and all metrics
for the 12 core/policy combinations were compared before and after the change
and remain exactly equal.

The additional 30 scenarios and 10 held-out scenarios use separate fixed
seeds. The first generated distribution exceeded the predeclared 0.10
TWAP–dump mean-score gap; no parameter or seed adjustment was needed. The
held-out split was used only for integrity checks. See the
[generation protocol](scenario-generation.md) for the ranges and limits.

## Interrupted core attempt

The Codex attempt `20261006T130110720321Z` stopped after four scored samples
with a CLI exit classified as requested-model unavailability or lack of
authorization. It is an incomplete experiment and is excluded in full from
the aggregate comparison. A subsequent smoke check using the identical model
and configuration succeeded. The replacement 18-sample attempt began at
`20261006T131255536371Z`, on the same source commit and Inspect version.
This recovery does not establish the provider-side cause of the interruption.

The first Claude core batch completed Sonnet's 18 samples, then stopped after
15 scored Opus samples when `price_cap`, epoch 3, returned a
`system/api_retry` notification. The installed Claude 2.1.290 schema identifies
this as a recoverable API-request retry. The adapter had rejected it as an
unknown system event. The fix validates its documented fields and retains the
notification while still requiring a successful final decision, consistent
model identity and no unexpected native actions.

Because this correction changed the evaluated source, the comparison was
restarted for all four models from commit
`68eec1732db240562f0f300bd6d63cbed90f44d8`, including the v0.2 extension. The
earlier successful Sonnet and Codex runs are retained locally
for audit but are excluded from the final comparison. No partial Opus scores
are combined with a later run. Each new model run gets its own manifest.

Inspect's `max_retries=0` prevents automatic retries of failed CLI invocations;
the runner stops on sample errors. It does not control HTTP retries inside a
CLI executable. A successfully returned decision can still contain invalid
tool arguments: after Inspect rejects them without executing the action, the
agent may make a new decision within the original limits. These are distinct
from both transport retries and sample restarts. The CLI track includes
internal HTTP retries within the fixed process timeout; only `advance()` moves
the simulated market.

After the `api_retry` correction, smoke run `20261006T160748198248Z` completed
one `deep_calm` sample for each of the three Claude models at the frozen commit,
using Inspect 0.3.276 and Claude Code 2.1.291. The installed 2.1.291 `api_retry`
schema was checked against 2.1.290 and is unchanged. Smoke samples and all
earlier-commit runs are excluded from the final core comparison.

## Final comparison validation

All four final manifests completed successfully with 18 unique scenario/epoch
pairs each, for 72 scored executions. The native Inspect logs and run manifests
are retained locally and are not distributed with the repository or review
archive. The published [`results/summary.json`](../results/summary.json)
contains per-sample metrics, aggregate results, and source log basenames and
SHA-256 hashes.

A separate local replay audit matched every sample's recorded actions, tool
results, fills, scorer explanation and metrics. All 1,189 model calls retained
their native responses. The 72 sets of public sample metrics, five aggregate
means per model and both standard-error definitions matched the audited data.
There were no sample errors, market-rule violations, no-fill samples or limit
terminations. Haiku had one recovered tool-argument parsing error. Opus and
Sonnet each left two orders incomplete; a successful evaluation does not imply
a fully completed purchase. See [the findings](findings.md) for exact cases.

Two generations of the report, with the manifest order reversed in the second,
produced byte-identical JSON, Markdown and PNG files using Matplotlib 3.11.2.
The final figure and the agreement between README, tables and JSON were also
reviewed. The final delivery adds documentation and results to the experimental
commit; the evaluated Python sources and dependency declaration are unchanged.
The 30 generated and 10 held-out scenarios have not been evaluated with these
real-model runs.

## Public author alias

On 7 October 2026, repository history was rewritten to use the author's public
alias, Albefero, in the README, licence and citation. The original experiment
commit IDs and log hashes remain in the results as recorded provenance.
Experimental commit `68eec1732db240562f0f300bd6d63cbed90f44d8` corresponds to
[`e7bce636e058d3769d3fa2d690cd31c51b8102b8`](https://github.com/albefero/execbench/tree/e7bce636e058d3769d3fa2d690cd31c51b8102b8)
in the rewritten history. Its Python sources and dependency declaration are
byte-identical; the recorded source SHA-256 remains
`c2d0273bfe5edab04b41fc65cdc74531cd4584792e06a3554d393ac328229f27`.
No evaluation was rerun and no scores were changed as part of this update.

## Delivery map

| Brief task | Main files | Change and rationale |
|---|---|---|
| Real-model evaluation | `execbench/cli_transport.py`, `execbench/cli_provider.py`, `scripts/run_cli_evals.py`, `docs/cli-evaluation.md` | Use the owner-requested logged-in CLIs while retaining the Inspect agent and deterministic executor. Reject unexpected native actions and model fallbacks; retain raw responses and explicit provenance. |
| Tables and chart | `scripts/results_table.py`, `results/`, `README.md` | Read native Inspect logs, validate complete compatible runs, and publish deterministic aggregates with both reference policies. Cost means exclude no-fill placeholders; score variability uses three complete-suite repetitions. |
| Failure analysis | `docs/findings.md` | Count behaviours observed in the final transcripts and identify concrete samples, epochs and short visible quotations. Separate costly choices, tool-argument errors and market-rule violations. |
| Generated scenarios | `execbench/generated.py`, `execbench/task.py`, `execbench/baselines.py`, `docs/scenario-generation.md` | Add fixed generated and held-out splits with selectable task sets. Narrow the budget margin to its intended case while preserving all core reference actions and metrics. |
| Repository hygiene | `pyproject.toml`, `.github/workflows/tests.yml`, `CITATION.cff`, `.gitignore` | Pin the evaluated Inspect version, add credential-free CI, identify the author, and exclude raw logs, credentials and the private briefing. |

Tests are in `tests/test_sim.py`, `test_agent_wiring.py`, `test_baselines.py`,
`test_generated.py`, `test_cli_transport.py`, `test_cli_provider.py`,
`test_cli_wiring.py`, `test_run_cli_evals.py` and `test_results_table.py`.
The complete local suite at the experimental commit finished with:

```text
315 passed in 8.21s
```

The core reference table is unchanged. Dependency validation passed.
[GitHub Actions](https://github.com/albefero/execbench/actions/workflows/tests.yml)
runs the dependency check and tests on pushes and pull requests.

## Explaining the design in an interview

- **Why separate proposal and execution?** The model chooses an action; the
  executor checks it, applies it and records what actually happened. A fluent
  final answer cannot replace evidence of trades or constraint compliance.
- **Why replay the market for each tool call?** A scenario and its ordered
  action list are enough to reconstruct the state and score. This avoids
  hidden mutable state between calls and makes discrepancies inspectable.
- **Why repeat fixed markets?** Repetitions expose variation in model
  decisions under the same conditions. They do not add independent market
  paths or establish performance in unfamiliar regimes.
- **Why keep a separate held-out split?** Choosing distributions from their
  scores would bias the evaluation. The split has distinct seeds and is not
  used for tuning; its public seeds do not make it contamination-proof.
- **Why document the CLI track separately?** The measured system includes the
  model, vendor CLI and adapter. Its prompts, defaults and formatting can
  influence decisions, so this experiment does not isolate a direct API model.
