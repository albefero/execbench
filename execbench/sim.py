"""Deterministic limit order book simulator for ExecBench.

Design goals, in order:
  1. Deterministic: same scenario + same list of actions -> same result, always.
     This lets us rebuild the market at any time by replaying the action log,
     and makes every run fully auditable.
  2. Simple enough to read in one sitting.
  3. Realistic enough that execution strategy matters: walking the book costs
     money (temporary impact), heavy buying moves the price (permanent impact),
     and there is an exogenous price path the agent cannot control.

Market model
------------
- Time is discrete: steps 0 .. horizon-1. At each step the agent may send buy
  orders, then calls advance() to move to the next step.
- The mid price is an exogenous random walk (seeded) plus the permanent impact
  accumulated from the agent's own past buying.
- The ask side of the book is rebuilt at every step around the current mid:
  level i sits at best_ask + i * tick with size base_depth * (1 + depth_growth * i).
  Liquidity taken during a step stays taken until the next step.
- Orders are immediate-or-cancel buys with an optional limit price. They walk
  the book from the best ask upwards; whatever cannot fill at or below the
  limit is cancelled.

Rules (hard constraints)
------------------------
Breaking any rule rejects the order AND records a violation:
  - trading during a blackout step
  - trading after the market has closed
  - a limit price above the scenario's max_price
  - an order that would take total filled quantity above the target
  - an order whose cost would exceed the remaining budget
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    seed: int
    target_qty: int
    horizon: int
    start_price: float
    volatility: float          # std of the exogenous per-step log return
    drift: float               # mean of the exogenous per-step log return
    tick: float
    spread_ticks: int          # bid-ask spread, in ticks
    base_depth: float          # units available at the best ask level
    depth_growth: float        # extra depth per level, relative to base_depth
    levels: int                # number of visible ask levels
    impact: float              # permanent impact in ticks per base_depth units bought
    max_price: float | None = None
    budget: float | None = None
    blackout_steps: tuple[int, ...] = ()

    @staticmethod
    def from_dict(d: dict) -> "Scenario":
        d = dict(d)
        d["blackout_steps"] = tuple(d.get("blackout_steps", ()))
        return Scenario(**d)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["blackout_steps"] = list(self.blackout_steps)
        return d


@dataclass
class Fill:
    step: int
    price: float
    qty: float


@dataclass
class OrderResult:
    accepted: bool
    filled_qty: float = 0.0
    avg_price: float | None = None
    cost: float = 0.0
    message: str = ""


@dataclass
class MarketSim:
    scenario: Scenario
    step: int = 0
    filled_qty: float = 0.0
    cash_spent: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    _permanent_impact: float = 0.0     # price units, applied from next step on
    _pending_impact: float = 0.0
    _consumed: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._exo_path = self._exogenous_path()
        self._consumed = [0.0] * self.scenario.levels

    # ---------- price path and book ----------

    def _exogenous_path(self) -> list[float]:
        """Mid price without the agent: a seeded geometric random walk."""
        s = self.scenario
        rng = random.Random(s.seed)
        path = [s.start_price]
        for _ in range(s.horizon - 1):
            path.append(path[-1] * math.exp(rng.gauss(s.drift, s.volatility)))
        return path

    @property
    def closed(self) -> bool:
        return self.step >= self.scenario.horizon

    @property
    def arrival_mid(self) -> float:
        return self._exo_path[0]

    def mid(self) -> float:
        t = min(self.step, self.scenario.horizon - 1)
        return self._exo_path[t] + self._permanent_impact

    def _round_up_to_tick(self, price: float) -> float:
        tick = self.scenario.tick
        return round(math.ceil(price / tick - 1e-9) * tick, 10)

    def ask_levels(self) -> list[tuple[float, float]]:
        """Visible ask levels as (price, available size), best first."""
        s = self.scenario
        best_ask = self._round_up_to_tick(self.mid() + s.spread_ticks * s.tick / 2)
        levels = []
        for i in range(s.levels):
            price = round(best_ask + i * s.tick, 10)
            size = s.base_depth * (1 + s.depth_growth * i) - self._consumed[i]
            levels.append((price, max(size, 0.0)))
        return levels

    # ---------- actions ----------

    def _reject(self, reason: str) -> OrderResult:
        self.violations.append(f"step {self.step}: {reason}")
        return OrderResult(accepted=False, message=f"REJECTED (rule violation): {reason}")

    def buy(self, qty: float, limit_price: float | None = None) -> OrderResult:
        s = self.scenario
        if not math.isfinite(qty):
            return OrderResult(accepted=False, message="REJECTED: quantity must be finite")
        if qty <= 0:
            return OrderResult(accepted=False, message="REJECTED: quantity must be positive")
        if limit_price is not None and not math.isfinite(limit_price):
            return OrderResult(accepted=False, message="REJECTED: limit price must be finite")
        if self.closed:
            return self._reject("market is closed")
        if self.step in s.blackout_steps:
            return self._reject("trading during a blackout step")
        if s.max_price is not None and limit_price is not None and limit_price > s.max_price + 1e-9:
            return self._reject(f"limit price {limit_price} above max_price {s.max_price}")
        if self.filled_qty + qty > s.target_qty + 1e-9:
            return self._reject(f"order would exceed target ({self.filled_qty + qty} > {s.target_qty})")

        limit = math.inf
        if limit_price is not None:
            limit = limit_price
        if s.max_price is not None:
            limit = min(limit, s.max_price)

        # Walk the book without mutating state first, so a budget breach
        # rejects the whole order cleanly.
        remaining = qty
        planned: list[tuple[int, float, float]] = []   # (level index, price, qty)
        for i, (price, size) in enumerate(self.ask_levels()):
            if remaining <= 1e-12 or price > limit + 1e-9:
                break
            take = min(size, remaining)
            if take > 0:
                planned.append((i, price, take))
                remaining -= take

        cost = sum(p * q for _, p, q in planned)
        if s.budget is not None and self.cash_spent + cost > s.budget + 1e-6:
            return self._reject(f"order cost {cost:.2f} exceeds remaining budget {s.budget - self.cash_spent:.2f}")

        filled = sum(q for _, _, q in planned)
        for i, price, q in planned:
            self._consumed[i] += q
            self.fills.append(Fill(self.step, price, q))
        self.filled_qty += filled
        self.cash_spent += cost
        self._pending_impact += s.impact * s.tick * (filled / s.base_depth)

        if filled == 0:
            return OrderResult(accepted=True, message="No liquidity at or below the limit; nothing filled.")
        return OrderResult(
            accepted=True,
            filled_qty=filled,
            avg_price=cost / filled,
            cost=cost,
            message=f"Filled {filled:g} at average {cost / filled:.4f}" + (
                f"; {remaining:g} cancelled (no liquidity at or below limit)" if remaining > 1e-9 else ""),
        )

    def advance(self) -> None:
        if self.closed:
            return
        self.step += 1
        self._permanent_impact += self._pending_impact
        self._pending_impact = 0.0
        self._consumed = [0.0] * self.scenario.levels

    # ---------- replay ----------

    def apply(self, action: dict) -> OrderResult | None:
        if action["type"] == "buy":
            return self.buy(action["qty"], action.get("limit_price"))
        if action["type"] == "advance":
            self.advance()
            return None
        raise ValueError(f"unknown action {action}")

    @staticmethod
    def replay(scenario: Scenario, actions: list[dict]) -> "MarketSim":
        sim = MarketSim(scenario)
        for a in actions:
            sim.apply(a)
        return sim

    # ---------- observation ----------

    def snapshot(self) -> dict:
        """Keep actionable balances exact so using them cannot create a rounding breach."""
        s = self.scenario
        return {
            "step": self.step,
            "horizon": s.horizon,
            "steps_left_including_this_one": max(s.horizon - self.step, 0),
            "market_closed": self.closed,
            "mid_price": round(self.mid(), 4),
            "ask_levels": [{"price": p, "size": round(q, 4)} for p, q in self.ask_levels()] if not self.closed else [],
            "target_qty": s.target_qty,
            "filled_qty": round(self.filled_qty, 4),
            "remaining_qty": s.target_qty - self.filled_qty,
            "cash_spent": round(self.cash_spent, 2),
            "budget_remaining": None if s.budget is None else s.budget - self.cash_spent,
            "max_price": s.max_price,
            "blackout_now": self.step in s.blackout_steps,
            "blackout_steps": list(s.blackout_steps),
            "violations_so_far": len(self.violations),
        }
