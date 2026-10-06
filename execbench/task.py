"""ExecBench as an Inspect evaluation.

How the pieces fit:

  dataset   one Sample per scenario; the scenario parameters travel in metadata
  setup     copies the scenario into the per-sample store and starts an empty action log
  agent     a ReAct agent with three tools (get_market_state, buy, advance)
  scorer    replays the action log on a fresh simulator and computes the metrics

The market is never held in memory between tool calls. Each tool call rebuilds
it by replaying the action log from the store. The simulator is deterministic,
so this is exact, and the log in the Inspect transcript is the full audit trail.

Run against a model:
    inspect eval execbench/task.py@execbench --model anthropic/claude-sonnet-5

Run the scripted reference policies (no model needed):
    inspect eval execbench/task.py@execbench_baseline -T policy=twap --model mockllm/model
    inspect eval execbench/task.py@execbench_baseline -T policy=dump --model mockllm/model
"""

from __future__ import annotations

import json

from inspect_ai import Task, task
from inspect_ai.agent import AgentPrompt, AgentSubmit, as_solver, react
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.scorer import Score, Target, mean, scorer, stderr
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import tool
from inspect_ai.util import store

from execbench.baselines import run_policy
from execbench.generated import select_scenarios
from execbench.scoring import evaluate
from execbench.sim import MarketSim, Scenario

SCENARIO_KEY = "execbench_scenario"
ACTIONS_KEY = "execbench_actions"


# ---------------------------------------------------------------- prompt

INSTRUCTIONS = """You are an execution trader. Your job is to BUY exactly {target_qty} units of an asset \
within {horizon} time steps (steps 0 to {last_step}), as cheaply as possible.

Scenario: {description}

How the market works:
- Call get_market_state() to see the current step, the mid price and the visible ask levels (price and size).
- Call buy(quantity, limit_price) to send an immediate-or-cancel buy order. It fills against the ask levels \
from the best price upwards, never above limit_price, and cancels whatever cannot fill.
- Buying consumes liquidity for the current step, and large buying pushes future prices up.
- Call advance() to move to the next step. The book refreshes at every step. You cannot go back.

Rules. Breaking any rule gets the order rejected and counts as a violation, which lowers your score:
- Never trade during a blackout step.{blackout_line}
- Never exceed the target quantity in total.{cap_line}{budget_line}

How you are scored: completion of the target, average price paid versus the mid price at step 0 \
(implementation shortfall), compared with a TWAP benchmark on the same market; violations are penalised.

Call tools one at a time, never in parallel. When the market has closed or you have bought the full \
quantity, call submit() with a one-line summary of what you did."""


def build_prompt(s: Scenario) -> str:
    return INSTRUCTIONS.format(
        target_qty=s.target_qty,
        horizon=s.horizon,
        last_step=s.horizon - 1,
        description=s.description,
        blackout_line=f" Blackout steps in this scenario: {list(s.blackout_steps)}." if s.blackout_steps else " There are no blackout steps in this scenario.",
        cap_line=f"\n- Never pay more than the maximum price of {s.max_price}." if s.max_price is not None else "",
        budget_line=f"\n- Never spend more than the total budget of {s.budget:,.2f}." if s.budget is not None else "",
    )


# ---------------------------------------------------------------- market access (shared by tools and scripted policies)

def _current_sim() -> MarketSim:
    scenario = Scenario.from_dict(store().get(SCENARIO_KEY))
    return MarketSim.replay(scenario, store().get(ACTIONS_KEY, []))


def _record(action: dict) -> None:
    actions = list(store().get(ACTIONS_KEY, []))
    actions.append(action)
    store().set(ACTIONS_KEY, actions)


def market_state() -> dict:
    return _current_sim().snapshot()


def place_buy(quantity: float, limit_price: float | None) -> dict:
    sim = _current_sim()
    action = {"type": "buy", "qty": float(quantity), "limit_price": limit_price}
    result = sim.apply(action)
    _record(action)
    return {
        "accepted": result.accepted,
        "filled_qty": round(result.filled_qty, 4),
        "avg_price": None if result.avg_price is None else round(result.avg_price, 4),
        "message": result.message,
        "filled_so_far": round(sim.filled_qty, 4),
        # An agent must be able to order the displayed remainder without
        # rounding it up into an over-target violation.
        "remaining_qty": sim.scenario.target_qty - sim.filled_qty,
    }


def advance_step() -> dict:
    sim = _current_sim()
    if sim.closed:
        return {"message": "Market already closed.", **sim.snapshot()}
    sim.advance()
    _record({"type": "advance"})
    return sim.snapshot()


# ---------------------------------------------------------------- tools

@tool
def get_market_state():
    async def execute() -> str:
        """Get the current market snapshot: step, mid price, visible ask levels, your progress and the rules.

        Returns:
            JSON with the current market state.
        """
        return json.dumps(market_state())

    return execute


@tool
def buy():
    async def execute(quantity: float, limit_price: float | None = None) -> str:
        """Send an immediate-or-cancel buy order at the current step.

        Args:
            quantity: Number of units to buy. Must be positive.
            limit_price: Highest price you accept per unit. Levels above it are not taken. Optional.

        Returns:
            JSON with the fill result and your updated progress.
        """
        return json.dumps(place_buy(quantity, limit_price))

    return execute


@tool
def advance():
    async def execute() -> str:
        """Move to the next time step. The order book refreshes. This cannot be undone.

        Returns:
            JSON with the market state at the new step.
        """
        return json.dumps(advance_step())

    return execute


# ---------------------------------------------------------------- solvers

@solver
def setup_market() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.store.set(SCENARIO_KEY, state.metadata["scenario"])
        state.store.set(ACTIONS_KEY, [])
        return state

    return solve


@solver
def scripted_policy(policy: str) -> Solver:
    """Replays a reference policy through the same tool layer the agent uses."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        scenario = Scenario.from_dict(state.metadata["scenario"])
        for action in run_policy(scenario, policy):
            if action["type"] == "buy":
                place_buy(action["qty"], action["limit_price"])
            else:
                advance_step()
        state.completed = True
        return state

    return solve


# ---------------------------------------------------------------- scorer

@scorer(metrics={
    "score": [mean(), stderr()],
    "completion": [mean()],
    "shortfall_bps": [mean()],
    "vs_twap_bps": [mean()],
    "violations": [mean()],
})
def execution_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        scenario = Scenario.from_dict(state.metadata["scenario"])
        actions = state.store.get(ACTIONS_KEY, [])
        r = evaluate(scenario, actions)
        return Score(
            value={
                "score": r["score"],
                "completion": r["completion"],
                # a model that never trades has no shortfall; report the worst sensible value
                "shortfall_bps": r["shortfall_bps"] if r["shortfall_bps"] is not None else 0.0,
                "vs_twap_bps": r["vs_twap_bps"] if r["vs_twap_bps"] is not None else 0.0,
                "violations": r["violations"],
            },
            explanation=json.dumps({k: v for k, v in r.items() if k != "violation_log"}),
            metadata={"violation_log": r["violation_log"], "n_actions": len(actions)},
        )

    return score


# ---------------------------------------------------------------- tasks

def _dataset(scenarios: str = "core") -> MemoryDataset:
    return MemoryDataset([
        Sample(id=s.id, input=build_prompt(s), target="", metadata={"scenario": s.to_dict()})
        for s in select_scenarios(scenarios)
    ])


@task
def execbench(message_limit: int = 160, scenarios: str = "core") -> Task:
    agent = react(
        prompt=AgentPrompt(
            instructions=None,
            assistant_prompt="Think briefly about your execution plan before acting. Use one tool call per message.",
        ),
        tools=[get_market_state(), buy(), advance()],
        submit=AgentSubmit(name="submit", description="Finish the task with a one-line summary."),
    )
    return Task(
        dataset=_dataset(scenarios),
        solver=[setup_market(), as_solver(agent)],
        scorer=execution_scorer(),
        message_limit=message_limit,
        config=GenerateConfig(parallel_tool_calls=False),
    )


@task
def execbench_baseline(policy: str = "twap", scenarios: str = "core") -> Task:
    return Task(
        dataset=_dataset(scenarios),
        solver=[setup_market(), scripted_policy(policy)],
        scorer=execution_scorer(),
    )
