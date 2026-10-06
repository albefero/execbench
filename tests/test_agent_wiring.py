"""End-to-end check of the agent path with a scripted mock model (no API key needed).

The mock model emits real tool calls: it buys 50 units per step for 10 steps
(a TWAP by hand) and then submits. If the tools, the store and the scorer are
wired correctly, the thin_book sample must score exactly like the TWAP baseline.
"""

from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, ModelUsage, get_model

from execbench.baselines import run_policy
from execbench.scenarios import BY_ID
from execbench.scoring import evaluate
from execbench.task import execbench


def _call(tool, args):
    out = ModelOutput.for_tool_call("mockllm/model", tool, args)
    out.usage = ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
    return out


def test_scripted_agent_matches_twap_on_thin_book(tmp_path, monkeypatch):
    # Keep Inspect's diagnostic trace alongside this test's temporary logs.
    monkeypatch.setenv("INSPECT_TRACE_FILE", str(tmp_path / "inspect-trace.log"))
    outputs = [_call("get_market_state", {})]
    for _ in range(10):
        outputs.append(_call("buy", {"quantity": 50, "limit_price": None}))
        outputs.append(_call("advance", {}))
    outputs.append(_call("submit", {"answer": "Bought 50 per step."}))

    model = get_model("mockllm/model", custom_outputs=outputs)
    [log] = inspect_eval(
        execbench(),
        model=model,
        sample_id="thin_book",
        log_dir=str(tmp_path / "logs"),
        log_format="json",
        log_realtime=False,
        display="none",
    )
    assert log.status == "success", log.error
    value = log.samples[0].scores["execution_scorer"].value
    assert value["completion"] == 1.0
    assert value["violations"] == 0
    assert value["score"] == 1.0
    expected_actions = run_policy(BY_ID["thin_book"], "twap")
    expected = evaluate(BY_ID["thin_book"], expected_actions)
    assert value["shortfall_bps"] == expected["shortfall_bps"]
    assert value["vs_twap_bps"] == expected["vs_twap_bps"]
    assert log.samples[0].store["execbench_actions"] == expected_actions
    assert log.samples[0].limit is None
