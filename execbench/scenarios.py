"""ExecBench v0 scenario set.

Each scenario stresses one execution skill. All share the same basic shape
(buy a fixed quantity within a fixed number of steps) so results are
comparable; what changes is the market and the rules.

  deep_calm      Liquid, quiet market. Sanity check: almost any sensible plan works.
  thin_book      Shallow book and strong impact. Buying too fast is expensive.
  blackout       Trading is forbidden in some steps. Tests rule-following under time pressure.
  price_cap      Hard maximum price close to the market. Must stay patient and within the cap.
  tight_budget   Budget barely covers the target. Overpaying means failing to complete.
  trending_up    Price drifts upward. Buying earlier than TWAP pays off; tests adaptation.
"""

from __future__ import annotations

from .sim import Scenario

_BASE = dict(
    start_price=100.0,
    tick=0.05,
    spread_ticks=2,
    levels=10,
    depth_growth=0.1,
    horizon=10,
    target_qty=500,
    drift=0.0,
)

SCENARIOS: list[Scenario] = [
    Scenario(
        id="deep_calm",
        description="Liquid, quiet market with no additional rules.",
        seed=11, volatility=0.0010, base_depth=400, impact=0.2,
        **_BASE,
    ),
    Scenario(
        id="thin_book",
        description="Shallow order book; your own buying moves the price noticeably.",
        seed=12, volatility=0.0010, base_depth=50, impact=0.3,
        **_BASE,
    ),
    Scenario(
        id="blackout",
        description="Trading is forbidden during an announcement window (steps 3, 4 and 5).",
        seed=13, volatility=0.0015, base_depth=70, impact=0.3,
        blackout_steps=(3, 4, 5),
        **_BASE,
    ),
    Scenario(
        id="price_cap",
        description="You must never pay more than the maximum price.",
        seed=16, volatility=0.0015, base_depth=70, impact=0.3,
        max_price=100.15,
        **_BASE,
    ),
    Scenario(
        id="tight_budget",
        description="Your budget barely covers the full quantity at current prices.",
        seed=15, volatility=0.0010, base_depth=60, impact=0.3,
        budget=50_090.0,
        **_BASE,
    ),
    Scenario(
        id="trending_up",
        description="Moderately liquid market.",
        seed=16, volatility=0.0010, base_depth=70, impact=0.3,
        **{**_BASE, "drift": 0.0015},
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}
