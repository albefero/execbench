"""Real Inspect integration with scripted CLI decisions and no external calls."""

from copy import deepcopy
import json

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import get_model

import execbench._registry  # noqa: F401: register the actual CLI ModelAPI classes
from execbench import cli_provider
from execbench.baselines import run_policy
from execbench.scenarios import BY_ID
from execbench.scoring import evaluate
from execbench.sim import MarketSim
from execbench.task import execbench


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_cli_provider_replays_twap_through_real_inspect(provider, tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECT_TRACE_FILE", str(tmp_path / "inspect-trace.log"))
    scenario = BY_ID["thin_book"]
    expected_actions = run_policy(scenario, "twap")
    decisions = [("get_market_state", {})]
    decisions.extend(
        ("buy", {"quantity": action["qty"], "limit_price": action["limit_price"]})
        if action["type"] == "buy"
        else ("advance", {})
        for action in expected_actions
    )
    decisions.append(("submit", {"answer": "Executed the scripted TWAP policy."}))
    requests = []

    async def scripted_decision(actual_provider, model, messages, tools, tool_choice, **kwargs):
        assert actual_provider == provider
        assert model == "test-model"
        assert len(requests) < len(decisions), "Inspect requested an unexpected extra decision"
        action, arguments = decisions[len(requests)]
        requests.append(deepcopy({"messages": messages, "tools": tools, "tool_choice": tool_choice}))
        return {
            "action": action,
            "arguments": arguments,
            "rationale": "Scripted integration test decision.",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "observed_model": "test-model",
            "raw_response": [{"type": "synthetic_test_response", "decision": len(requests)}],
        }

    monkeypatch.setattr(cli_provider, "request_decision", scripted_decision)
    task = execbench()
    private_canary = "PRIVATE_METADATA_MUST_NEVER_REACH_THE_MODEL_4c81"
    for sample in task.dataset:
        sample.metadata["private_test_canary"] = private_canary

    [log] = inspect_eval(
        task,
        model=get_model(f"{provider}_cli/test-model"),
        sample_id=scenario.id,
        log_dir=str(tmp_path / "logs"),
        log_format="json",
        display="none",
        max_retries=0,
        log_model_api=True,
        log_realtime=False,
    )

    assert log.status == "success", log.error
    assert len(log.samples) == 1
    sample = log.samples[0]
    assert sample.error is None
    assert sample.limit is None
    assert sample.store["execbench_actions"] == expected_actions
    expected = evaluate(scenario, expected_actions)
    value = sample.scores["execution_scorer"].value
    assert value == {key: expected[key] for key in value}
    assert value["score"] == 1.0
    assert value["completion"] == 1.0
    assert value["violations"] == 0
    assert len(requests) == len(decisions)
    model_events = [event for event in sample.events if event.event == "model"]
    assert len(model_events) == len(decisions) > 5
    final_call = model_events[-1].call
    assert final_call is not None
    assert final_call.response["observed_model"] == "test-model"
    assert final_call.response["model_identity_verified"] is True
    assert final_call.response["raw_response"] == [
        {"type": "synthetic_test_response", "decision": len(decisions)}
    ]

    # The task really holds private metadata, while the transport receives only
    # visible conversation messages and the public tool specifications.
    assert sample.metadata["private_test_canary"] == private_canary
    assert sample.store["execbench_scenario"]["seed"] == scenario.seed
    serialized_requests = json.dumps(requests)
    assert private_canary not in serialized_requests
    assert '"seed"' not in serialized_requests
    assert '"execbench_scenario"' not in serialized_requests
    assert '"metadata"' not in serialized_requests
    for request in requests:
        assert {tool["name"] for tool in request["tools"]} == {
            "get_market_state", "buy", "advance", "submit"
        }
        for message in request["messages"]:
            assert set(message) <= {
                "role", "content", "tool_calls", "tool_call_id", "function", "error"
            }

    initial_observations = [
        json.loads(message["content"])
        for message in requests[1]["messages"]
        if message["role"] == "tool" and message.get("function") == "get_market_state"
    ]
    assert initial_observations == [MarketSim(scenario).snapshot()]

    # Verify the public reader can recover the persisted audit trail, rather
    # than relying only on the in-memory EvalLog returned by the evaluation.
    persisted = read_eval_log(log.location)
    assert persisted.status == "success"
    assert len(persisted.samples) == 1
    persisted_sample = persisted.samples[0]
    assert persisted_sample.store["execbench_actions"] == expected_actions
    assert persisted_sample.scores["execution_scorer"].value == value
    persisted_events = [event for event in persisted_sample.events if event.event == "model"]
    assert len(persisted_events) == len(decisions)
    assert persisted_events[-1].call.response == final_call.response
