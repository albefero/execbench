# ExecBench

An [Inspect](https://inspect.aisi.org.uk/) benchmark that measures how well AI agents execute a large order in a market with limited liquidity, price impact and hard rules.

Most agent benchmarks test whether a model can find an answer. ExecBench tests something closer to economic work: acting over many steps with money at stake, where every action changes the environment and some mistakes cannot be undone. The task is one that trading desks solve every day and that has a standard, well-understood yardstick (implementation shortfall against TWAP), which makes results easy to interpret.

## The task

The agent must buy a fixed quantity of an asset within a fixed number of time steps, as cheaply as possible. It has three tools:

- `get_market_state()` returns the step, the mid price, the visible ask levels and its progress.
- `buy(quantity, limit_price)` sends an immediate-or-cancel order that walks the book from the best ask upwards.
- `advance()` moves to the next step. The book refreshes; the agent cannot go back.

Buying consumes liquidity for the current step (temporary impact) and pushes future prices up (permanent impact). The mid price also follows an exogenous random walk the agent does not control. Scenarios add hard rules: blackout steps, a maximum price, a total budget. Breaking a rule rejects the order and counts as a violation.

## Scenarios (v0)

| Scenario | What it tests |
|---|---|
| `deep_calm` | Liquid, quiet market. Sanity check. |
| `thin_book` | Shallow book, strong impact. Buying too fast is expensive. |
| `blackout` | Trading forbidden in steps 3 to 5. Rule-following under time pressure. |
| `price_cap` | Hard maximum price close to the market, which the market drifts above. Patience alone fails. |
| `tight_budget` | Budget barely covers the target. Overpaying means not completing. |
| `trending_up` | Upward drift. Adapting and buying earlier than TWAP pays off. |

## Scoring

Execution quality is measured with implementation shortfall, the average price paid versus the mid price at step 0, in basis points. Each agent run is compared with a TWAP executed on the same scenario and the same exogenous price path, so luck in the random walk cancels out.

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

## Running it

```bash
pip install -e ".[dev]"

# against a model (needs the provider's API key in the environment)
inspect eval execbench/task.py@execbench --model anthropic/claude-sonnet-5

# reference policies, no API key needed
inspect eval execbench/task.py@execbench_baseline -T policy=twap --model mockllm/model
inspect eval execbench/task.py@execbench_baseline -T policy=dump --model mockllm/model

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

No real-model results have been published yet. The reference table above
contains scripted policies only.

## Design choices

- **Deterministic simulator, replayed from an action log.** The market is never held in memory between tool calls. Every call rebuilds it by replaying the agent's actions from the per-sample store. This keeps the implementation stateless, makes every run exactly reproducible, and turns the Inspect transcript into a complete audit trail.
- **Same exogenous path for agent and baseline.** Comparing against TWAP on the identical random walk isolates execution skill from market luck.
- **Violations are counted on attempts, not only on outcomes.** An order that would break a rule is rejected before it touches the book. This measures whether the agent reads and respects constraints, which matters as much as price for any agent trusted with money.
- **Temporary impact dominates permanent impact.** Walking the book is more expensive than the permanent drift your own buying causes, which is the regime observed in practice and the one in which splitting an order is worth it.
- **One documented scale constant.** `COST_SCALE_BPS = 10` is fixed across scenarios and not tuned per scenario.

## Limitations and roadmap

v0 is deliberately small. Planned for v1:

- 30 to 50 scenarios generated from parameter ranges, with held-out seeds
- sell-side execution and two-sided books with resting limit orders
- partial observability (hidden liquidity, delayed data)
- a human baseline from execution traders
- multi-asset treasury management (TreasuryBench)

## Author

Albefero · [LinkedIn](https://www.linkedin.com/in/albefero)

Licensed under the MIT License.
