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
The smoke run `20261006T085315879123Z` completed successfully for all four
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

`_affordable_qty` applies its 0.1% budget margin whenever available quantity
is below the requested quantity, including some cases where liquidity or a
price cap is the binding constraint. The six core reference results are not
affected. The extension corrects this behaviour and adds focused regression
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

Because this correction changes the evaluated source, the full comparison is
restarted for all four models from the same new commit, including the v0.2
extension. The earlier successful Sonnet and Codex runs are retained locally
for audit but are excluded from the final comparison. No partial Opus scores
are combined with a later run. Each new model run gets its own manifest.

Inspect's `max_retries=0` prevents framework-level decision retries. It does
not control HTTP retries inside a CLI executable. The documented CLI track
includes that internal transport behaviour within the fixed process timeout;
it must not be described as a protocol with no retries at any layer.
