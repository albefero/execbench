# ExecBench

An [Inspect](https://inspect.aisi.org.uk/) benchmark that measures how well AI agents execute a large order in a market with limited liquidity, price impact and hard rules.

The agent must balance completion, execution cost and hard constraints over a sequence of decisions. Markets are synthetic and seeded; every trade and rejection can be reproduced from the action log. Implementation shortfall measures cost relative to the initial mid price; comparison with a time-weighted average price (TWAP) policy provides a reference for execution quality.

## The task

The agent must buy a fixed quantity of an asset within a fixed number of time steps, as cheaply as possible. It has three tools:

- `get_market_state()` returns the step, the mid price, the visible ask levels and its progress.
- `buy(quantity, limit_price)` sends an immediate-or-cancel order that walks the book from the best ask upwards.
- `advance()` moves to the next step. The book refreshes; the agent cannot go back.

A separate `submit()` action ends the agent's run with a short summary.

Buying consumes liquidity for the current step (temporary impact) and pushes future prices up (permanent impact). The mid price also follows an exogenous random walk the agent does not control. Scenarios add hard rules: blackout steps, a maximum price, a total budget. Breaking a rule rejects the order and counts as a violation.

## Scenarios

The six original **core** scenarios remain fixed:

| Scenario | What it tests |
|---|---|
| `deep_calm` | Liquid, quiet market. Sanity check. |
| `thin_book` | Shallow book, strong impact. Buying too fast is expensive. |
| `blackout` | Trading forbidden in steps 3 to 5. Rule-following under time pressure. |
| `price_cap` | Hard maximum price close to the market, which the market drifts above. Patience alone fails. |
| `tight_budget` | Budget barely covers the target. Overpaying means not completing. |
| `trending_up` | Upward drift. Adapting and buying earlier than TWAP pays off. |

Version 0.2 adds **30 generated** scenarios and **10 held-out** scenarios with
separate fixed seeds. They vary liquidity, impact, volatility, drift, blackout
windows, price caps and budgets. The held-out set is reserved for evaluation;
its outcomes are not used to choose parameters. Select a set with
`-T scenarios=core`, `generated`, `heldout` or `all`; the default remains `core`.
Published core results do not imply that models have been evaluated on these
additional scenarios. See [the generation protocol](docs/scenario-generation.md)
for parameter ranges and seed rules.

## Scoring

Execution quality is measured with implementation shortfall, the average price paid versus the mid price at step 0, in basis points. Each agent run is compared with TWAP on the same seeded exogenous price path. This controls for differences between market paths; execution timing and the agent's own price impact still affect the outcome.

```
score      = completion * cost_score * 0.75 ** violations
completion = filled / target
cost_score = 1 / (1 + max(agent_bps - twap_bps, 0) / 10)
```

A score of 1.0 means the order was fully executed, at least as cheaply as TWAP, with no rule broken. `vs_twap_bps` is reported uncapped, so agents that beat TWAP show by how much.

## Reference results

Scripted policies, no model involved. They show that the benchmark separates reasonable execution from naive execution.

| Scenario | TWAP score | TWAP IS (bps) | Dump score | Dump IS (bps) |
|---|---|---|---|---|
| deep_calm | 1.00 | -1.0 | 0.59 | 6.0 |
| thin_book | 1.00 | 5.0 | 0.35 | 23.2 |
| blackout | 1.00 | 18.0 | 0.99 | 18.1 |
| price_cap | 0.80 | 3.1 | 0.66 | 8.3 |
| tight_budget | 1.00 | 14.0 | 0.61 | 20.3 |
| trending_up | 1.00 | 78.0 | 1.00 | 18.1 |
| **mean** | **0.97** | | **0.70** | |

"Dump" buys everything at the first opportunity. TWAP itself fails to complete in `price_cap` and is beaten by 60 bps in `trending_up`: the benchmark leaves room for agents to do better than the baseline, not only to match it. Regenerate this table with `python scripts/baseline_table.py`.

## Model results

**CLI track, 6 October 2026:** six fixed scenarios × three executions × four
models, for 72 scored executions. All four runs completed successfully with
Inspect **0.3.276**, Python **3.11.15**, Claude Code **2.1.291** and Codex CLI
**0.160.1**, using source commit
`68eec1732db240562f0f300bd6d63cbed90f44d8`.
The equivalent [source checkout](https://github.com/albefero/execbench/tree/e7bce636e058d3769d3fa2d690cd31c51b8102b8)
is available after the [author-alias history update](docs/review-notes.md#public-author-alias).

| Requested CLI model | Score ± repeat SEM | Completion | IS (bps) | vs TWAP (bps) | Violations |
|---|---:|---:|---:|---:|---:|
| `claude_cli/claude-haiku-4-5-20251001` | 0.854 ± 0.013 | 100.0% | 20.43 | -0.92 | 0.00 |
| `claude_cli/claude-opus-5-5` | 0.983 ± 0.010 | 98.3% | 19.05 | 0.46 | 0.00 |
| `claude_cli/claude-sonnet-5` | 0.932 ± 0.029 | 98.3% | 19.95 | -0.44 | 0.00 |
| `codex_cli/gpt-6-astra` | 0.824 ± 0.044 | 100.0% | 22.69 | -3.17 | 0.00 |

The ± value is the standard error of three whole-suite epoch means. With only
three repeats on six fixed markets, it does not establish generalisation or
statistically significant differences between models. Positive `vs TWAP`
means cheaper execution. Cost columns are unweighted means across executions
with fills and describe only the units actually traded; completion must be
read alongside them. Violations are mean counts per execution.

These are requested CLI identifiers. Codex supplied no observed model
identifier, so its label records the request only. Reported identifiers are
checked for consistency, not independently verified against the hosted backend.
This track measures the model, CLI and JSON adapter together;
see [the protocol](docs/cli-evaluation.md).

![Mean execution score across six fixed scenarios, with scripted TWAP and dump references](results/scores_by_scenario.png)

See [the complete results](results/results.md), [machine-readable metrics and
provenance](results/summary.json), and [the transcript findings](docs/findings.md).
The figure was generated with Matplotlib 3.11.2.

## Running it

```bash
pip install -e ".[dev]"

# against a model (needs the provider's API key in the environment)
inspect eval execbench/task.py@execbench --model anthropic/claude-sonnet-5

# reference policies, no API key needed
inspect eval execbench/task.py@execbench_baseline -T policy=twap --model mockllm/model
inspect eval execbench/task.py@execbench_baseline -T policy=dump --model mockllm/model

# generated scenarios, using the same agent and scoring rules
inspect eval execbench/task.py@execbench -T scenarios=generated --model anthropic/claude-sonnet-5

# tests
pytest
```

### Using logged-in CLI sessions

The `claude_cli` and `codex_cli` Inspect providers use Claude Code and Codex CLI
through their normal logged-in sessions. They do not extract credentials or
reuse OAuth tokens through a different API client.

```bash
# First validate the CLI/model combinations on one scenario each.
python scripts/run_cli_evals.py --smoke

# Then run the six core scenarios, three epochs per model.
python scripts/run_cli_evals.py
```

These runs require the installed CLIs, working network access and permission
for each CLI to manage its own local runtime state. Raw logs and the run
manifest are saved under `logs/` and are excluded from Git.

CLI results measure **model + CLI + JSON adapter**. They must be labelled as a
separate track from direct API results: the CLI contributes its own prompts,
defaults and formatting behaviour. The Inspect agent, tools, market, scoring
formula and 160-message limit stay the same. See
[the evaluation protocol](docs/cli-evaluation.md) for isolation, limits and
reproducibility details.

## Design choices

- **Deterministic simulator, replayed from an action log.** The market is never held in memory between tool calls. Every call rebuilds it by replaying the agent's actions from the per-sample store. Replaying the recorded actions reproduces market state, fills, rejections and scores. Fresh hosted-model evaluations can produce different actions even with the same recorded configuration.
- **Same exogenous path for agent and baseline.** Both policies face the same seeded external price path. This controls for differences between paths; the selected scenarios and seeds still affect the comparison.
- **Violations are counted on attempts, not only on outcomes.** An order that would break a rule is rejected before it touches the book. This measures whether the agent reads and respects constraints, which matters as much as price for any agent trusted with money.
- **Explicit synthetic market assumptions.** The scenarios include regimes where walking the book makes immediate execution expensive, alongside cases where buying early helps. Their parameters are not calibrated to a real exchange.
- **One documented scale constant.** `COST_SCALE_BPS = 10` is fixed across scenarios and not tuned per scenario.

## Limitations and roadmap

The core comparison covers six fixed market paths. Repeating model executions
estimates variation on those paths; it does not create additional independent
markets. CLI results also include each CLI's system instructions, defaults and
formatting behaviour. They should not be read as direct API results or evidence
of profitable real-world trading.

The simulator has a single asset, buy orders, visible liquidity and discrete
time steps. Only `advance()` moves simulated time; model computation and network
waiting do not move the market. It omits fees, latency, queue position and
competing traders. The score saturates when an agent matches or beats TWAP, so
the uncapped
`vs_twap_bps` metric is needed to distinguish better execution. Incomplete
orders have cost statistics only for the quantity that actually traded.

Future extensions:

- sell-side execution and two-sided books with resting limit orders
- partial observability (hidden liquidity, delayed data)
- a human baseline from execution traders
- multi-asset treasury management (TreasuryBench)

## Reproducibility and citation

The published experiment identifies its source commit, Inspect version,
requested model identifiers and CLI versions in `results/summary.json`.
Inspect is pinned to that version. Tests use scripted model outputs and require
no API keys. Raw transcripts remain under the ignored `logs/` directory; only
reviewed results and selected evidence are included in the repository. The
recorded versions identify the evaluated protocol; they do not guarantee
identical future responses from a hosted model.

The [review notes](docs/review-notes.md) explain the implementation changes
and validation.

See [CITATION.cff](CITATION.cff) to cite the software and
[the evaluation protocol](docs/cli-evaluation.md) for limits and provenance.

## Author

Albefero · [LinkedIn](https://www.linkedin.com/in/albefero)

Licensed under the MIT License.
