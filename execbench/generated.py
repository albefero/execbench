"""Fixed v0.2 scenario distributions, separate from the six core markets.

Parameter ranges and seeds are chosen before model evaluation. Both splits
use the same five constraint families; held-out seeds must not guide tuning.
Market seeds are separate from parameter-generation seeds and from core seeds.

Ranges: best-level depth 35--90 units; permanent impact 0.10--0.30 ticks per
base-depth purchase; volatility 0.0002--0.0010 and drift -0.0002--0.0002 per
step; blackouts of 1--3 steps starting at step 2--6; caps 2--8 ticks above
arrival; budgets 10--30 bps above target quantity times arrival price.
"""

from __future__ import annotations

import random

from .scenarios import SCENARIOS
from .sim import Scenario


def generate_scenarios(*, heldout: bool = False) -> list[Scenario]:
    """Regenerate a split without global random state or data-dependent filtering."""
    rng = random.Random(61002 if heldout else 61001)
    count = 10 if heldout else 30
    seed_base = 200_000 if heldout else 100_000
    prefix = "heldout" if heldout else "generated"
    scenarios = []
    for index in range(count):
        family = index % 5
        base_depth = rng.randint(35, 90)
        impact = round(rng.uniform(0.10, 0.30), 4)
        volatility = round(rng.uniform(0.0002, 0.0010), 6)
        drift = round(rng.uniform(-0.0002, 0.0002), 6)
        blackout_start = rng.randint(2, 6)
        blackout_length = rng.randint(1, 3)
        cap_ticks = rng.randint(2, 8)
        budget_bps = rng.randint(10, 30)
        scenarios.append(Scenario(
            id=f"{prefix}_{index + 1:02d}",
            description="An execution market with limited liquidity and the rules listed below.",
            seed=seed_base + index,
            target_qty=500,
            horizon=10,
            start_price=100.0,
            volatility=volatility,
            drift=drift,
            tick=0.05,
            spread_ticks=2,
            base_depth=base_depth,
            depth_growth=0.1,
            levels=10,
            impact=impact,
            max_price=round(100.0 + cap_ticks * 0.05, 2) if family in (2, 4) else None,
            budget=round(50_000.0 * (1 + budget_bps / 10_000), 2) if family in (3, 4) else None,
            blackout_steps=tuple(range(blackout_start, blackout_start + blackout_length))
            if family in (1, 4) else (),
        ))
    return scenarios


def select_scenarios(name: str = "core") -> list[Scenario]:
    """Keep the original benchmark as the default; expanded sets are opt-in."""
    if name == "core":
        return list(SCENARIOS)
    if name == "generated":
        return generate_scenarios()
    if name == "heldout":
        return generate_scenarios(heldout=True)
    if name == "all":
        return list(SCENARIOS) + generate_scenarios() + generate_scenarios(heldout=True)
    raise ValueError("scenarios must be one of: core, generated, heldout, all")
