"""Scoring for ExecBench.

Main metric: implementation shortfall (IS), the standard execution-quality
measure used by trading desks. It is the average price paid versus the mid
price at the moment the order arrived (step 0), in basis points:

    shortfall_bps = (avg_fill_price - arrival_mid) / arrival_mid * 10_000

Lower is better. The agent is compared with a TWAP run on the same scenario
and the same exogenous price path, so luck in the random walk cancels out.

Score in [0, 1]:

    score = completion * cost_score * 0.75 ** violations

    completion = filled / target                              (capped at 1)
    excess     = max(agent_bps - twap_bps, 0)                 (how much worse than TWAP)
    cost_score = 1 / (1 + excess / 10)                        (10 bps worse -> 0.5)

A score of 1.0 means "fully executed, at least as cheaply as TWAP, no rule
broken". vs_twap_bps is reported uncapped, so agents that beat TWAP show it.
The 10 bps scale is a documented constant (COST_SCALE_BPS), not tuned per
scenario.
"""

from __future__ import annotations

from .baselines import run_policy
from .sim import MarketSim, Scenario

COST_SCALE_BPS = 10.0
VIOLATION_PENALTY = 0.75


def shortfall_bps(sim: MarketSim) -> float | None:
    if sim.filled_qty <= 0:
        return None
    avg = sim.cash_spent / sim.filled_qty
    return (avg - sim.arrival_mid) / sim.arrival_mid * 10_000


def evaluate(scenario: Scenario, actions: list[dict]) -> dict:
    agent = MarketSim.replay(scenario, actions)
    twap = MarketSim.replay(scenario, run_policy(scenario, "twap"))

    completion = min(agent.filled_qty / scenario.target_qty, 1.0)
    agent_bps = shortfall_bps(agent)
    twap_bps = shortfall_bps(twap)

    if agent_bps is None:
        cost_score = 0.0
    else:
        excess = max(agent_bps - (twap_bps if twap_bps is not None else 0.0), 0.0)
        cost_score = 1.0 / (1.0 + excess / COST_SCALE_BPS)

    violations = len(agent.violations)
    score = completion * cost_score * (VIOLATION_PENALTY ** violations)

    return {
        "score": round(score, 4),
        "completion": round(completion, 4),
        "shortfall_bps": None if agent_bps is None else round(agent_bps, 2),
        "twap_shortfall_bps": None if twap_bps is None else round(twap_bps, 2),
        "vs_twap_bps": None if agent_bps is None or twap_bps is None else round(twap_bps - agent_bps, 2),
        "violations": violations,
        "violation_log": agent.violations,
    }
