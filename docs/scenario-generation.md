# Generated scenarios

Version 0.2 adds 30 generated scenarios and 10 held-out scenarios. The six core
scenarios retain their original definitions. The expanded sets have not been
used in the core model comparison.

Each split cycles evenly through five constraint families: no additional rule,
blackout only, price cap only, budget only, and all three rules together.
Parameters are sampled independently with a local Python random generator;
generation does not depend on global random state or model performance.

| Parameter | Fixed range or value |
|---|---|
| Target / horizon | 500 units / 10 steps |
| Initial mid / tick | 100 / 0.05 |
| Spread / book levels | 2 ticks / 10 levels |
| Depth growth per level | 0.1 |
| Best-level depth | 35–90 units, inclusive integers |
| Permanent impact | 0.10–0.30 ticks per base-depth quantity bought |
| Per-step log-return volatility | 0.0002–0.0010 |
| Per-step log-return drift | −0.0002–0.0002 |
| Blackout | 1–3 consecutive steps, starting at step 2–6 |
| Price cap, where applicable | 100.10–100.40, in ticks of 0.05 |
| Budget, where applicable | 50,050–50,150, in increments of 5 |

Parameter-generation seeds are `61001` for generated and `61002` for held-out.
Market seeds are `100000`–`100029` and `200000`–`200009`, respectively. Both
ranges are disjoint from the core market seeds. Scenario IDs identify the split
and index. Descriptions expose the trading task and applicable rules without
revealing future prices or the sampled drift.

These ranges and seeds were fixed before checking their reference scores. The
acceptance threshold was a generated-set mean score gap of at least 0.10 in
favour of TWAP. The first configuration met it: TWAP **0.85817667**, dump
**0.58796000**, a gap of **0.27021667**. No seeds were discarded or selected
based on their scores. The distribution deliberately includes liquidity
constraints where splitting can help; it is not a calibrated sample of real
market conditions.

Held-out scenarios are checked for determinism, valid execution and adherence
to simulator rules. Their aggregate performance is not used to choose the
distribution, tune policies or select models. They are public fixed seeds,
not a secret test set or protection against future training contamination.

Use either task with `-T scenarios=core`, `generated`, `heldout` or `all`.
The default remains `core`; `all` contains 46 scenarios. The CLI experiment
runner intentionally continues to run only the six core scenarios.

During this extension, the reference policy's 0.1% budget margin was narrowed
to orders actually constrained by budget. Exhausting book depth or hitting a
price cap alone no longer applies that margin. All actions and metrics for the
12 original core/policy combinations remain exactly unchanged. The simulator,
scoring formula, core definitions and 160-message limit are unchanged.
