"""Budget headroom must not be confused with unavailable book liquidity."""

from dataclasses import replace
import math

from execbench.baselines import _affordable_qty, run_policy
from execbench.scenarios import BY_ID
from execbench.sim import MarketSim


def test_nonbinding_budget_does_not_reduce_available_depth():
    scenario = replace(BY_ID["thin_book"], levels=1, budget=1_000_000.0)
    sim = MarketSim(scenario)
    quantity = _affordable_qty(sim, scenario.target_qty)

    assert quantity == 50
    result = sim.buy(quantity)
    assert result.accepted and result.filled_qty == 50
    assert sim.ask_levels()[0][1] == 0


def test_nonbinding_budget_does_not_reduce_liquidity_under_price_cap():
    scenario = replace(BY_ID["price_cap"], budget=1_000_000.0)
    sim = MarketSim(scenario)
    available = sum(size for price, size in sim.ask_levels() if price <= scenario.max_price)

    quantity = _affordable_qty(sim, scenario.target_qty)
    result = sim.buy(quantity, scenario.max_price)

    assert result.accepted and result.filled_qty == available
    assert all(size == 0 for price, size in sim.ask_levels() if price <= scenario.max_price)


def test_budget_exactly_covering_available_depth_needs_no_margin():
    scenario = replace(BY_ID["thin_book"], levels=1, budget=50 * 100.05)
    sim = MarketSim(scenario)
    quantity = _affordable_qty(sim, scenario.target_qty)
    result = sim.buy(quantity)

    assert result.accepted and result.filled_qty == 50
    assert math.isclose(sim.cash_spent, scenario.budget, abs_tol=1e-6)
    assert not sim.violations


def test_binding_budget_keeps_margin_and_respects_previous_spending():
    scenario = replace(BY_ID["deep_calm"], budget=1_000.0)
    sim = MarketSim(scenario)
    sim.buy(2)
    remaining_budget = scenario.budget - sim.cash_spent
    best_price = sim.ask_levels()[0][0]

    quantity = _affordable_qty(sim, scenario.target_qty - sim.filled_qty)
    assert 0 < quantity < remaining_budget / best_price
    result = sim.buy(quantity)

    assert result.accepted and result.filled_qty == quantity
    assert sim.cash_spent < scenario.budget
    assert not sim.violations


def test_dump_can_take_all_depth_when_budget_is_nonbinding():
    scenario = replace(BY_ID["thin_book"], levels=1, horizon=1, budget=1_000_000.0)
    actions = run_policy(scenario, "dump")
    sim = MarketSim.replay(scenario, actions)

    assert sim.filled_qty == 50
    assert not sim.violations
