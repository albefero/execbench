# Execution outcomes and failure analysis

This analysis covers the 72 completed core CLI samples from 6 October 2026:
four models, each repeating the same six seeded markets three times. All used
Inspect 0.3.276 and commit `68eec1732db240562f0f300bd6d63cbed90f44d8`. Earlier attempts and smoke checks are
excluded. See the [results](../results/results.md) and
[CLI protocol](cli-evaluation.md).

## Definitions and frequency

These descriptive categories were selected during development and transcript
review; they are not preregistered failure rates. Categories overlap, and
initial concentration is counted only when at least 90% of the target was
filled at step 0 **and** the realised unit cost exceeded TWAP. Higher cost
means average cost per filled unit, not total cash spent; unequal completion
can make that comparison a trade-off rather than a uniformly worse outcome.

Counts below are samples out of 18 per model, except the final column, which
gives both error events and affected samples. Cost comparisons use an excess
greater than `1e-9` bps, and incompletion uses more than `1e-9` units remaining,
to exclude floating-point residue. Every counted cost difference is also
negative in the published `vs_twap_bps`; no counted deficit is hidden by the
published completion rounding.

| Model | Higher unit cost | Expensive initial concentration | Incomplete target | Argument errors: events / samples |
|---|---:|---:|---:|---:|
| Sonnet 5 | 6 | 1 | 2 | 0 / 0 |
| Opus 5.5 | 1 | 0 | 2 | 0 / 0 |
| Haiku 4.5 | 11 | 2 | 0 | 1 / 1 |
| Codex GPT-6-astra | 13 | 7 | 0 | 0 / 0 |

Across all 72 samples there were **zero economic rule violations, zero message
or time limits, and zero samples without fills**. The recovered tool error
below is separate from these quantities and from an evaluation error.

## Higher unit cost despite respecting the budget

Codex's `tight_budget`, epoch 1, completed all 500 units by step 1, spending
50,089.50 of the 50,090 budget. It paid 3.90 bps more per unit than TWAP and
scored 0.7194. Its final purchase explained:

> Visible liquidity can fill the remaining 248 units for 24,864.00, completing 500 units with 0.50 left in the budget.

Source: Codex `tight_budget`, epoch 1, model call 7, event index 39.
The budget arithmetic was correct; staying within the budget did not make
the execution as cheap as the reference schedule.

The magnitude varies considerably. Opus's only higher-cost case, `blackout`,
epoch 3, was approximately 0.012857 bps worse than TWAP (0.01 bps at the
reported precision). Frequency alone does not describe economic severity.

## Expensive initial concentration

Codex's `thin_book`, epochs 1 and 3, bought all 500 units in the first step
at an average price of 100.232. Each paid 18.20 bps more than TWAP and scored
0.3546. The epoch-1 decision explained:

> The visible asks offer all 500 units at or below 100.40, allowing completion before buying pushes future prices up.

Source: Codex `thin_book`, epoch 1, model call 2, event index 14.
In epoch 2 of the same seeded scenario, Codex bought 50 units per step,
matched TWAP and scored 1.0. This contrast describes observed decisions;
it does not identify a stable internal strategy.

## Incomplete targets under the price cap

All four incomplete samples occurred in `price_cap` and respected its 100.15
maximum. Their exact quantities were:

| Model | Scenario | Epoch | Filled / 500 | Remaining | Score |
|---|---|---:|---:|---:|---:|
| Sonnet 5 | `price_cap` | 1 | 425 | 75 | 0.85 |
| Sonnet 5 | `price_cap` | 3 | 420 | 80 | 0.84 |
| Opus 5.5 | `price_cap` | 2 | 450 | 50 | 0.90 |
| Opus 5.5 | `price_cap` | 3 | 400 | 100 | 0.80 |

TWAP also completed only 400 units in this scenario. Opus's epoch-3 sample
bought 50 units at each of steps 0–7 and submitted at step 9 with 100 units
remaining. Its explanation accurately identified the current price:

> The best ask is 100.40, above the 100.15 maximum

Source: Opus `price_cap`, epoch 3, model call 19, event index 99; excerpt.
A capped IOC order at that point would have been legal but would have filled
nothing. The incomplete outcome is not a price-rule violation, nor proof
that every policy had to leave the target unfinished: Codex and Haiku
completed all 500 units in all three epochs of this scenario.

Completion matters when interpreting unit cost. Codex's three `price_cap`
samples paid 0.41 bps more per unit than TWAP but bought 500 rather than 400
units and scored 0.9611. That cost difference alone does not establish that
executing fewer units would have been preferable.

## Recovered tool-argument error

Haiku's `tight_budget`, epoch 3, had already bought 500 units when it called
`submit` with empty arguments. Inspect rejected the missing required `answer`
before executing the tool. The next model call supplied the argument and
completed the sample, explaining:

> The previous submit() call failed due to missing required 'answer' parameter.

Source: Haiku `tight_budget`, epoch 3; rejected model call 7/event 39,
validation error at event 41, corrected model call 8/event 43.
There was one such error event in one sample across the four models.
The market remained at step 2 with five recorded actions, 500 units filled,
50,078.60 spent and zero economic violations. This was a new agent decision
after normal tool feedback, not a restarted evaluation sample or an internal
CLI request retry.

## Complete sample references

The lists identify every counted sample. Numbers after each scenario are
epoch numbers; parentheses in the concentration rows give units filled at
step 0.

| Category | Model | Scenario: epochs |
|---|---|---|
| Higher unit cost | Sonnet 5 | `blackout`: 2, 3; `deep_calm`: 2; `price_cap`: 2; `thin_book`: 2; `tight_budget`: 2 |
| Higher unit cost | Opus 5.5 | `blackout`: 3 |
| Higher unit cost | Haiku 4.5 | `blackout`: 1, 3; `deep_calm`: 1, 2, 3; `price_cap`: 1, 2, 3; `tight_budget`: 1, 2, 3 |
| Higher unit cost | Codex GPT-6-astra | `blackout`: 1, 2, 3; `deep_calm`: 1, 2, 3; `price_cap`: 1, 2, 3; `thin_book`: 1, 3; `tight_budget`: 1, 3 |
| Expensive initial concentration | Sonnet 5 | `deep_calm`: 2 (500 units) |
| Expensive initial concentration | Opus 5.5 | None |
| Expensive initial concentration | Haiku 4.5 | `deep_calm`: 2 (500 units), 3 (500 units) |
| Expensive initial concentration | Codex GPT-6-astra | `blackout`: 1 (500 units), 3 (500 units); `deep_calm`: 1 (500 units), 2 (500 units), 3 (500 units); `thin_book`: 1 (500 units), 3 (500 units) |
| Incomplete target | Sonnet 5 | `price_cap`: 1, 3 |
| Incomplete target | Opus 5.5 | `price_cap`: 2, 3 |
| Incomplete target | Haiku 4.5 | None |
| Incomplete target | Codex GPT-6-astra | None |
| Recovered argument error | Sonnet 5 | None |
| Recovered argument error | Opus 5.5 | None |
| Recovered argument error | Haiku 4.5 | `tight_budget`: 3 |
| Recovered argument error | Codex GPT-6-astra | None |

## What this says about money under constraints

The observed examples show that satisfying hard limits and completing an order can coexist with higher execution costs, as Codex's `tight_budget` epoch 1 illustrates. Completion and unit cost need to be read together, because the `price_cap` policies executed different quantities and the TWAP reference itself stopped at 400 units. Haiku's recovered submission error shows interface validation containing a mistake without changing market state, while the purchase decisions still require a separate economic assessment. Three repetitions of six synthetic markets support these descriptive findings, but do not establish reliable handling of real funds or performance on unseen markets.

## Audit and provenance

During the local audit, all 72 samples were read with Inspect's `read_eval_log`,
using `resolve_attachments=True`. Replay reproduced the tool results, recorded
actions, final state, score values, scorer explanations and violation logs.
The 72 sets of published sample metrics, each model's five aggregate means,
and both reported standard errors were checked against the replay.
No inconsistency was found. All 1,189 native call responses were retained in
the local logs:

| Model | Model calls / native responses retained | Reported model ID matched |
|---|---:|---:|
| Sonnet 5 | 413 / 413 | 413 |
| Opus 5.5 | 360 / 360 | 360 |
| Haiku 4.5 | 234 / 234 | 234 |
| Codex GPT-6-astra | 182 / 182 | 0 |

The Codex label is the requested model identifier. Its CLI did not return an
observed identifier, so all 182 calls correctly record
`model_identity_verified: false`; native confirmation is unavailable for that
track. The Claude logs contain no observed `api_retry` frames in this round;
that does not prove the absence of all lower-level retries.

The published [summary](../results/summary.json) contains per-sample metrics,
aggregate results, and the source log basenames and SHA-256 hashes for the four
completed core runs. Raw logs and run manifests are retained locally and are
not distributed with this repository. The published metrics allow aggregate
calculations to be checked; replaying the original actions or independently
checking transcript quotations requires the unpublished logs.

Model-call ordinals start at 1; event indices start at 0. Every quotation is
under 40 words and comes from `ModelCall.response.rationale`, the visible
explanation accompanying an action. It is evidence of what the agent said,
not a measurement of its internal reasoning.
