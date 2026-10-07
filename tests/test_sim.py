import math
from copy import deepcopy
from dataclasses import replace

from execbench.baselines import run_policy
from execbench.scenarios import BY_ID, SCENARIOS
from execbench.scoring import evaluate
from execbench.sim import MarketSim


def test_replay_is_deterministic():
    for s in SCENARIOS:
        actions = run_policy(s, "twap")
        a = MarketSim.replay(s, actions)
        b = MarketSim.replay(s, actions)
        assert a.cash_spent == b.cash_spent and a.filled_qty == b.filled_qty


def test_blackout_trade_is_rejected_and_counted():
    s = BY_ID["blackout"]
    sim = MarketSim(s)
    for _ in range(3):
        sim.advance()
    r = sim.buy(10)
    assert not r.accepted and sim.filled_qty == 0 and len(sim.violations) == 1


def test_limit_above_cap_is_a_violation_and_cap_is_enforced():
    s = BY_ID["price_cap"]
    sim = MarketSim(s)
    assert not sim.buy(10, limit_price=s.max_price + 1).accepted
    assert len(sim.violations) == 1
    sim.buy(400)  # no limit given: the cap still applies
    assert all(f.price <= s.max_price for f in sim.fills)


def test_exceeding_target_is_rejected():
    s = BY_ID["deep_calm"]
    sim = MarketSim(s)
    assert not sim.buy(s.target_qty + 1).accepted
    assert len(sim.violations) == 1


def test_budget_breach_is_rejected_whole():
    s = BY_ID["tight_budget"]
    sim = MarketSim(s)
    sim.cash_spent = s.budget - 10  # almost no money left
    r = sim.buy(50)
    assert not r.accepted and sim.filled_qty == 0


def test_buying_moves_future_prices_up():
    s = BY_ID["thin_book"]
    quiet, active = MarketSim(s), MarketSim(s)
    active.buy(200)
    quiet.advance(); active.advance()
    assert active.mid() > quiet.mid()


def test_twap_scores_one_when_it_completes_and_dump_is_punished_in_thin_book():
    s = BY_ID["thin_book"]
    assert evaluate(s, run_policy(s, "twap"))["score"] == 1.0
    assert evaluate(s, run_policy(s, "dump"))["score"] < 0.5


def test_doing_nothing_scores_zero():
    for s in SCENARIOS:
        assert evaluate(s, [])["score"] == 0.0


def test_nonfinite_quantity_is_rejected_without_changing_state():
    for qty in (float("nan"), float("inf"), float("-inf")):
        sim = MarketSim(BY_ID["deep_calm"])
        sim.buy(10)
        before = deepcopy(sim.__dict__)

        result = sim.buy(qty)

        assert not result.accepted
        assert sim.__dict__ == before


def test_nonfinite_limit_cannot_bypass_cap_or_change_state():
    for limit in (float("nan"), float("inf"), float("-inf")):
        sim = MarketSim(BY_ID["price_cap"])
        sim.buy(10)
        before = deepcopy(sim.__dict__)

        result = sim.buy(490, limit_price=limit)

        assert not result.accepted
        assert sim.__dict__ == before


def test_observed_remaining_quantity_can_be_bought_after_fractional_fill():
    sim = MarketSim(BY_ID["deep_calm"])
    sim.buy(0.333333)

    result = sim.buy(sim.snapshot()["remaining_qty"])

    assert result.accepted
    assert math.isclose(sim.filled_qty, sim.scenario.target_qty, rel_tol=0, abs_tol=1e-9)
    assert not sim.violations


def test_observed_remaining_budget_can_be_spent_after_fractional_fill():
    # A small budget keeps this order within the visible best-price liquidity.
    scenario = replace(BY_ID["deep_calm"], budget=1_000.0)
    sim = MarketSim(scenario)
    sim.buy(0.123456)
    state = sim.snapshot()
    affordable_qty = state["budget_remaining"] / state["ask_levels"][0]["price"]

    result = sim.buy(affordable_qty)

    assert result.accepted
    assert math.isclose(sim.cash_spent, scenario.budget, rel_tol=0, abs_tol=1e-6)
    assert not sim.violations


def test_observed_book_can_price_a_budget_limited_order_after_fractional_fill():
    sim = MarketSim(BY_ID["tight_budget"])
    assert sim.buy(0.12344).accepted
    state = sim.snapshot()
    money = state["budget_remaining"]
    quantity = 0.0
    # Price the next order using only the book and balance shown to the agent.
    # Rounding up the cheapest level would underestimate its execution cost.
    for level in state["ask_levels"]:
        take = min(level["size"], money / level["price"])
        quantity += take
        money -= take * level["price"]
        if money <= 1e-9:
            break
    assert 0 < quantity <= state["remaining_qty"]

    result = sim.buy(quantity)

    assert result.accepted
    assert math.isclose(sim.cash_spent, sim.scenario.budget, rel_tol=0, abs_tol=1e-6)
    assert not sim.violations


def test_budget_is_cumulative_and_rejected_order_preserves_prior_purchase():
    scenario = replace(BY_ID["deep_calm"], budget=300.0)
    sim = MarketSim(scenario)
    first = sim.buy(2)
    assert first.accepted and first.filled_qty == 2
    before = deepcopy(sim.__dict__)

    # Each order fits the initial budget, but together they exceed it.
    result = sim.buy(1)

    assert not result.accepted and result.filled_qty == 0 and result.cost == 0
    assert len(sim.violations) == 1 and "budget" in sim.violations[0]
    after = deepcopy(sim.__dict__)
    after["violations"] = before["violations"]
    assert after == before  # Only the violation log may change on rejection.


def test_closed_market_stays_closed_and_rejects_further_purchases():
    sim = MarketSim(BY_ID["deep_calm"])
    assert sim.buy(10).accepted
    for _ in range(sim.scenario.horizon):
        sim.advance()
    assert sim.closed and sim.snapshot()["ask_levels"] == []
    before = deepcopy(sim.__dict__)

    for _ in range(3):
        sim.advance()
    assert sim.__dict__ == before

    result = sim.buy(1)

    assert not result.accepted and result.filled_qty == 0 and result.cost == 0
    assert len(sim.violations) == 1 and "market is closed" in sim.violations[0]
    after = deepcopy(sim.__dict__)
    after["violations"] = before["violations"]
    assert after == before
