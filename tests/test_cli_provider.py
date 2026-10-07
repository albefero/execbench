"""The CLI adapter must preserve Inspect's real tools and accounting."""

import asyncio

import pytest
from inspect_ai.model import ChatMessageUser, GenerateConfig
from inspect_ai.tool import ToolInfo

from execbench import cli_provider
from execbench.cli_provider import ClaudeCLI, CodexCLI, model_usage, visible_messages


def test_codex_usage_does_not_double_count_cached_input():
    usage = model_usage("codex", {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 7})
    assert usage.input_tokens == 40
    assert usage.input_tokens_cache_read == 60
    assert usage.total_tokens == 107
    assert usage.total_cost is None


def test_claude_usage_includes_cache_reads_and_writes_once():
    usage = model_usage("claude", {
        "input_tokens": 40, "cache_read_input_tokens": 60,
        "cache_creation_input_tokens": 10, "output_tokens": 7,
    })
    assert usage.input_tokens == 40
    assert usage.total_tokens == 117
    assert usage.total_cost is None


def test_missing_usage_is_not_fabricated():
    with pytest.raises(ValueError, match="input_tokens"):
        model_usage("codex", {})
    with pytest.raises(ValueError, match="token usage"):
        model_usage("claude", None)


def test_only_visible_message_content_is_passed_on():
    message = ChatMessageUser(content="Visible market state", id="internal-message-id")
    assert visible_messages([message]) == [{"role": "user", "content": "Visible market state"}]


def test_cli_decision_becomes_one_inspect_tool_call(monkeypatch):
    requests = []

    async def fake_request(provider, model, messages, tools, choice, **kwargs):
        requests.append((provider, model, messages, tools, choice, kwargs))
        return {
            "action": "buy", "arguments": {"quantity": 50, "limit_price": 100.1},
            "rationale": "Buy the next scheduled slice.",
            "requested_model": model, "observed_model": model,
            "usage": {"input_tokens": 20, "output_tokens": 5},
            "raw_response": {"fixture": True},
        }

    monkeypatch.setattr(cli_provider, "request_decision", fake_request)
    api = ClaudeCLI("test-model")
    tool = ToolInfo(name="buy", description="Buy a slice")
    output, call = asyncio.run(api.generate(
        [ChatMessageUser(content="Buy 500 units.")], [tool], "auto",
        GenerateConfig(parallel_tool_calls=False),
    ))
    assert len(output.message.tool_calls) == 1
    action = output.message.tool_calls[0]
    assert action.function == "buy" and action.arguments["quantity"] == 50
    assert output.message.text == "Buy the next scheduled slice."
    assert output.usage.total_tokens == 25
    assert requests[0][0] == "claude"
    assert call.request["track"] == "cli"
    assert call.response["raw_response"] == {"fixture": True}
    assert call.response["model_identity_verified"] is True


@pytest.mark.parametrize("api_class", [ClaudeCLI, CodexCLI])
@pytest.mark.parametrize("observed_model", ["different-model", None])
def test_observed_model_cannot_silently_change_identity(monkeypatch, api_class, observed_model):
    async def fake_request(*args, **kwargs):
        return {
            "action": "submit", "arguments": {"answer": "Finished."},
            "rationale": "The target is complete.", "observed_model": observed_model,
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }

    monkeypatch.setattr(cli_provider, "request_decision", fake_request)
    api = api_class("requested-model")
    request = api.generate(
        [ChatMessageUser(content="Finish the task.")],
        [ToolInfo(name="submit", description="Finish")], "auto", GenerateConfig(),
    )
    if observed_model is not None:
        with pytest.raises(ValueError, match="exact model ID"):
            asyncio.run(request)
    else:
        output, call = asyncio.run(request)
        assert output.message.tool_calls[0].function == "submit"
        assert call.response["observed_model"] is None
        assert call.response["model_identity_verified"] is False


def test_unsupported_generation_settings_fail_before_calling_cli():
    api = CodexCLI("test-model")
    with pytest.raises(ValueError, match="temperature"):
        asyncio.run(api.generate([], [], "auto", GenerateConfig(temperature=0)))
    with pytest.raises(ValueError, match="sequential"):
        asyncio.run(api.generate([], [], "auto", GenerateConfig(parallel_tool_calls=True)))


@pytest.mark.parametrize("setting", [
    {"stop_seqs": ["END"]}, {"best_of": 2}, {"frequency_penalty": 1},
    {"presence_penalty": 1}, {"logit_bias": {42: 1}}, {"num_choices": 3},
    {"logprobs": True}, {"top_logprobs": 2}, {"prompt_logprobs": 2},
    {"internal_tools": True}, {"cache_prompt": False}, {"verbosity": "high"},
    {"effort": "max"}, {"reasoning_mode": "pro"}, {"reasoning_tokens": 100},
    {"reasoning_summary": "detailed"},
    {"response_schema": {"name": "other", "json_schema": {"type": "object"}}},
    {"extra_headers": {"X-Test": "fixture"}}, {"extra_body": {"other": True}},
    {"modalities": ["image"]}, {"fallback_models": ["other-model"]}, {"batch": True},
])
@pytest.mark.parametrize("api_class", [ClaudeCLI, CodexCLI])
def test_model_controls_cannot_be_silently_ignored(monkeypatch, setting, api_class):
    async def must_not_call(*args, **kwargs):
        raise AssertionError("Unsupported settings must fail before CLI invocation")

    monkeypatch.setattr(cli_provider, "request_decision", must_not_call)
    with pytest.raises(ValueError, match=next(iter(setting))):
        asyncio.run(api_class("fixture").generate([], [], "auto", GenerateConfig(**setting)))


def test_framework_controls_and_supported_reasoning_effort_remain_available(monkeypatch):
    calls = []

    async def fake_request(*args, **kwargs):
        calls.append(kwargs)
        return {
            "action": "submit", "arguments": {"answer": "Done"},
            "rationale": "Done", "observed_model": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr(cli_provider, "request_decision", fake_request)
    config = GenerateConfig(
        max_retries=0, timeout=20, attempt_timeout=10, max_connections=1,
        adaptive_connections=False, max_tool_output=1000, cache=False, batch=False,
        system_message="Fixture", reasoning_history="none", reasoning_effort="high",
        parallel_tool_calls=False,
    )
    output, call = asyncio.run(ClaudeCLI("fixture").generate(
        [ChatMessageUser(content="Finish")], [ToolInfo(name="submit", description="Finish")],
        "auto", config,
    ))
    assert output.message.tool_calls[0].function == "submit"
    assert calls[0]["reasoning_effort"] == call.request["reasoning_effort"] == "high"
