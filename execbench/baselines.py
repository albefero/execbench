"""Reference execution policies.

These do not use a model. They produce an action log that can be replayed on
the simulator, exactly like an agent's actions would be.

- twap: split the remaining quantity evenly across the remaining tradable
  (non-blackout) steps, respecting max_price and budget. This is the benchmark
  the agent is scored against.
- dump: try to buy everything at the first tradable step. Useful to show that
  the benchmark punishes naive execution in thin markets.
"""

from __future__ import annotations

from .sim import MarketSim, Scenario


def _tradable_steps_left(sim: MarketSim) -> int:
    s = sim.scenario
    return sum(1 for t in range(sim.step, s.horizon) if t not in s.blackout_steps)


def _affordable_qty(sim: MarketSim, qty: float) -> float:
    """Largest quantity <= qty that fits the remaining budget at current book prices."""
    s = sim.scenario
    if s.budget is None:
        return qty
    money = s.budget - sim.cash_spent
    limit = s.max_price if s.max_price is not None else float("inf")
    take_total = 0.0
    budget_binds = False
    for price, size in sim.ask_levels():
        if price > limit or take_total >= qty:
            break
        available = min(size, qty - take_total)
        affordable = max(money, 0.0) / price
        budget_binds = budget_binds or available > affordable
        take = min(available, affordable)
        take_total += take
        money -= take * price
    if take_total >= qty - 1e-9:
        return qty
    # Exhausting depth or reaching a price cap does not justify a budget margin.
    return take_total * 0.999 if budget_binds else take_total


def run_policy(scenario: Scenario, policy: str) -> list[dict]:
    sim = MarketSim(scenario)
    actions: list[dict] = []
    while not sim.closed:
        remaining = scenario.target_qty - sim.filled_qty
        tradable_now = sim.step not in scenario.blackout_steps
        if remaining > 1e-9 and tradable_now:
            if policy == "twap":
                qty = remaining / max(_tradable_steps_left(sim), 1)
            elif policy == "dump":
                qty = remaining
            else:
                raise ValueError(policy)
            qty = _affordable_qty(sim, qty)
            if qty > 1e-9:
                action = {"type": "buy", "qty": qty, "limit_price": scenario.max_price}
                sim.apply(action)
                actions.append(action)
        sim.advance()
        actions.append({"type": "advance"})
    return actions
