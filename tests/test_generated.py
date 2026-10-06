"""Validate generated distributions without tuning against held-out performance."""

import math
import random
import statistics

import pytest

from execbench.baselines import run_policy
from execbench.generated import generate_scenarios, select_scenarios
from execbench.scenarios import SCENARIOS
from execbench.scoring import evaluate
from execbench.sim import MarketSim
from execbench.task import execbench, execbench_baseline


@pytest.mark.parametrize("heldout", [False, True])
def test_generation_is_deterministic_without_using_global_randomness(heldout):
    before = random.getstate()
    first = generate_scenarios(heldout=heldout)
    second = generate_scenarios(heldout=heldout)

    assert [scenario.to_dict() for scenario in first] == [scenario.to_dict() for scenario in second]
    assert random.getstate() == before


def test_splits_have_expected_sizes_and_disjoint_market_seeds():
    generated = generate_scenarios()
    heldout = generate_scenarios(heldout=True)
    core_seeds = {scenario.seed for scenario in SCENARIOS}
    generated_seeds = {scenario.seed for scenario in generated}
    heldout_seeds = {scenario.seed for scenario in heldout}

    assert len(generated) == len(generated_seeds) == 30
    assert len(heldout) == len(heldout_seeds) == 10
    assert not core_seeds & (generated_seeds | heldout_seeds)
    assert not generated_seeds & heldout_seeds
    assert len({scenario.id for scenario in SCENARIOS + generated + heldout}) == 46


def test_core_selection_preserves_original_scenarios():
    selected = select_scenarios()

    assert [scenario.id for scenario in selected] == [
        "deep_calm", "thin_book", "blackout", "price_cap", "tight_budget", "trending_up",
    ]
    assert selected == SCENARIOS
    assert selected is not SCENARIOS


@pytest.mark.parametrize("heldout", [False, True])
def test_parameter_ranges_and_constraint_families(heldout):
    scenarios = generate_scenarios(heldout=heldout)
    families = set()
    for scenario in scenarios:
        assert 35 <= scenario.base_depth <= 90
        assert 0.10 <= scenario.impact <= 0.30
        assert 0.0002 <= scenario.volatility <= 0.0010
        assert -0.0002 <= scenario.drift <= 0.0002
        assert scenario.target_qty == 500 and scenario.horizon == 10
        if scenario.blackout_steps:
            assert 1 <= len(scenario.blackout_steps) <= 3
            assert 2 <= min(scenario.blackout_steps) <= 6
            assert max(scenario.blackout_steps) < scenario.horizon
        assert scenario.max_price is None or 100.10 <= scenario.max_price <= 100.40
        assert scenario.budget is None or 50_050 <= scenario.budget <= 50_150
        families.add((bool(scenario.blackout_steps), scenario.max_price is not None, scenario.budget is not None))
    assert families == {
        (False, False, False), (True, False, False), (False, True, False),
        (False, False, True), (True, True, True),
    }


@pytest.mark.parametrize("name,size", [("core", 6), ("generated", 30), ("heldout", 10), ("all", 46)])
def test_both_tasks_select_the_requested_scenarios(name, size):
    expected = [scenario.id for scenario in select_scenarios(name)]
    for task in (execbench(scenarios=name), execbench_baseline(scenarios=name)):
        assert len(task.dataset) == size
        assert [sample.id for sample in task.dataset] == expected
    assert execbench(scenarios=name).config.parallel_tool_calls is False


def test_unknown_scenario_set_is_rejected():
    with pytest.raises(ValueError, match="scenarios must be"):
        execbench(scenarios="misspelled")


@pytest.mark.parametrize("heldout", [False, True])
@pytest.mark.parametrize("policy", ["twap", "dump"])
def test_reference_policies_finish_with_finite_metrics_and_respect_rules(heldout, policy):
    for scenario in generate_scenarios(heldout=heldout):
        actions = run_policy(scenario, policy)
        sim = MarketSim.replay(scenario, actions)
        metrics = evaluate(scenario, actions)

        assert sim.closed
        assert 0 <= sim.filled_qty <= scenario.target_qty + 1e-9
        assert math.isfinite(sim.cash_spent)
        assert not sim.violations
        assert 0 <= metrics["score"] <= 1
        assert 0 <= metrics["completion"] <= 1
        for name in ("shortfall_bps", "twap_shortfall_bps", "vs_twap_bps"):
            assert metrics[name] is None or math.isfinite(metrics[name])
        assert scenario.budget is None or sim.cash_spent <= scenario.budget + 1e-6
        for fill in sim.fills:
            assert fill.step not in scenario.blackout_steps
            assert scenario.max_price is None or fill.price <= scenario.max_price


def test_generated_set_rewards_splitting_by_a_prespecified_mean_gap():
    # A 0.10 gap was specified before evaluating these fixed seeds. Held-out
    # performance is deliberately excluded from this distribution-design check.
    scenarios = generate_scenarios()
    means = {
        policy: statistics.mean(evaluate(s, run_policy(s, policy))["score"] for s in scenarios)
        for policy in ("twap", "dump")
    }
    assert means["twap"] - means["dump"] >= 0.10, means
