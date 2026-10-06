"""Exercise the CLI contract without invoking a model or reading credentials."""

import asyncio
import json
from pathlib import Path
import signal

import pytest

from execbench import cli_transport as transport


TOOLS = [
    {"name": "get_market_state", "description": "Observe the market.", "parameters": {"type": "object"}},
    {"name": "buy", "description": "Buy units.", "parameters": {"type": "object"}},
    {"name": "submit", "description": "Finish.", "parameters": {"type": "object"}},
]
MESSAGES = [{"role": "user", "content": "Buy 500 units."}]
DECISION = {"action": "buy", "arguments_json": '{"quantity":50,"limit_price":null}', "rationale": "Split the order."}


def codex_events(decision=None):
    return [
        {"type": "thread.started", "thread_id": "fixture"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(decision or DECISION)}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 15}},
    ]


def claude_events(decision=None):
    return [
        {"type": "system", "subtype": "init", "tools": ["StructuredOutput"], "model": "claude-model-snapshot"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "format-1", "name": "StructuredOutput", "input": decision or DECISION},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "format-1", "content": "valid"},
        ]}},
        {"type": "result", "subtype": "success", "is_error": False,
         "structured_output": decision or DECISION,
         "usage": {"input_tokens": 20, "cache_read_input_tokens": 80, "output_tokens": 15}},
    ]


@pytest.fixture
def fake_cli(monkeypatch):
    """A fake subprocess records argv/cwd/stdin; no executable is launched."""
    calls = []

    def install(events=None, *, stdout=None, stderr=b"", status=0, delay=0):
        active = 0
        encoded = stdout if stdout is not None else (
            "\n".join(json.dumps(event) for event in events) + "\n"
        ).encode()

        class Process:
            pid = 123456
            returncode = None

            async def communicate(self, data):
                nonlocal active
                calls[-1]["stdin"] = data.decode()
                try:
                    await asyncio.sleep(delay)
                    self.returncode = status
                    return encoded, stderr
                finally:
                    active -= 1

            async def wait(self):
                self.returncode = -signal.SIGKILL
                return self.returncode

            def kill(self):
                self.returncode = -signal.SIGKILL

        async def create(*args, **kwargs):
            nonlocal active
            active += 1
            cwd = Path(kwargs["cwd"])
            calls.append({
                "argv": args, "options": kwargs,
                "files": sorted(p.name for p in cwd.iterdir()), "active_at_start": active,
            })
            return Process()

        monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", create)
        return calls

    return install


def request(provider="codex", **kwargs):
    return transport.request_decision(
        provider, "requested-model", MESSAGES, TOOLS,
        workspace_root=Path(__file__).resolve().parents[2], **kwargs,
    )


def test_prompt_excludes_metadata_and_preserves_visible_tool_results():
    messages = [
        {"role": "user", "content": "Trade carefully.", "metadata": {"seed": 991}},
        {"role": "tool", "content": '{"step":1}', "function": "advance", "tool_call_id": "call-1"},
    ]
    tools = [{**TOOLS[0], "metadata": {"secret_scenario": "hidden"}}]
    prompt = transport.build_prompt(messages, tools, "required")
    assert "Trade carefully." in prompt
    assert "call-1" in prompt
    assert "991" not in prompt
    assert "metadata" not in prompt
    assert "hidden" not in prompt
    assert prompt == transport.build_prompt(messages, tools, "required")


def test_codex_success_has_fresh_context_and_deny_profile(fake_cli):
    events = codex_events()
    calls = fake_cli(events)
    result = asyncio.run(request(reasoning_effort="medium"))
    assert result["action"] == "buy"
    assert result["arguments"] == {"quantity": 50, "limit_price": None}
    assert result["usage"] == events[-1]["usage"]
    assert result["requested_model"] == "requested-model"
    assert result["observed_model"] is None
    assert result["raw_response"] == events
    assert len(calls) == 1
    call = calls[0]
    argv = call["argv"]
    assert "--ignore-user-config" in argv
    assert "--ephemeral" in argv
    assert "--strict-config" in argv
    assert "--ignore-rules" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "features.shell_tool=false" in argv
    assert "project_doc_max_bytes=0" in argv
    assert "memories.use_memories=false" in argv
    assert 'model_reasoning_effort="medium"' in argv
    profile = next(arg for arg in argv if arg.startswith("permissions="))
    assert 'extends=":read-only"' in profile
    assert f'"{Path(__file__).resolve().parents[2]}"="deny"' in profile
    assert call["files"] == ["decision.schema.json"]
    assert call["options"]["start_new_session"] is True
    assert not Path(call["options"]["cwd"]).exists()


def test_claude_success_keeps_oauth_and_disables_tools(fake_cli):
    events = claude_events()
    calls = fake_cli(events)
    result = asyncio.run(request("claude"))
    assert result["observed_model"] == "claude-model-snapshot"
    assert result["usage"] == events[-1]["usage"]
    argv = calls[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--safe-mode" in argv
    assert "--restricted" in argv
    assert "--bare" not in argv
    assert "--strict-mcp-config" in argv
    assert "mcp__*" in argv
    assert "--no-session-persistence" in argv
    assert calls[0]["files"] == []


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"])
def test_codex_native_tool_activity_is_rejected(fake_cli, item_type):
    events = codex_events()
    events.insert(2, {"type": "item.started", "item": {"type": item_type}})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="tool activity"):
        asyncio.run(request())


@pytest.mark.parametrize("tool_name", ["Read", "Bash", "mcp__server__read"])
def test_claude_native_tools_are_rejected(fake_cli, tool_name):
    events = claude_events()
    events[1]["message"]["content"][0]["name"] = tool_name
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="tool activity"):
        asyncio.run(request("claude"))


def test_claude_orphan_tool_results_are_rejected(fake_cli):
    events = claude_events()
    events[2]["message"]["content"][0]["tool_use_id"] = "unseen-read"
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="tool result"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("arguments", ["[]", "null", '{"quantity":NaN}', '{"quantity":Infinity}', '{"nested":[1e999]}', '{"quantity":'])
def test_invalid_arguments_fail_without_retries(fake_cli, arguments):
    calls = fake_cli(codex_events({**DECISION, "arguments_json": arguments}))
    with pytest.raises(transport.CLITransportError):
        asyncio.run(request())
    assert len(calls) == 1


def test_unavailable_action_is_rejected(fake_cli):
    fake_cli(codex_events({**DECISION, "action": "read_seed"}))
    with pytest.raises(transport.CLITransportError, match="unavailable action"):
        asyncio.run(request())


def test_forced_tool_choice_is_enforced(fake_cli):
    fake_cli(codex_events())
    with pytest.raises(transport.CLITransportError, match="unavailable action"):
        asyncio.run(request(tool_choice={"type": "function", "function": {"name": "submit"}}))
    with pytest.raises(ValueError, match="tool_choice"):
        transport.decision_schema(TOOLS, "none")


@pytest.mark.parametrize("provider,events", [
    ("codex", [{"type": "turn.failed", "error": {"message": "secret-token"}}]),
    ("claude", [{"type": "result", "subtype": "error_max_turns", "is_error": True}]),
])
def test_provider_error_events_are_errors(fake_cli, provider, events):
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="failed") as caught:
        asyncio.run(request(provider))
    assert "secret-token" not in str(caught.value)


def test_exit_errors_withhold_cli_diagnostics(fake_cli):
    fake_cli(stdout=b"secret-stdout", stderr=b"token=secret-stderr" * 1000, status=2)
    with pytest.raises(transport.CLITransportError) as caught:
        asyncio.run(request())
    assert "status 2" in str(caught.value)
    assert "secret-" not in str(caught.value)
    assert len(str(caught.value)) < 180


@pytest.mark.parametrize("diagnostic,category", [
    (b"Read-only file system", "local runtime or filesystem permission failure"),
    (b"EACCES", "local runtime or filesystem permission failure"),
    (b"Permission denied", "local runtime or filesystem permission failure"),
    (b"Operation not permitted", "local runtime or filesystem permission failure"),
    (b"getaddrinfo EAI_AGAIN", "network or DNS connectivity failure"),
    (b"Could not resolve host", "network or DNS connectivity failure"),
    (b"Temporary failure in name resolution", "network or DNS connectivity failure"),
    (b"Connection refused", "network or DNS connectivity failure"),
    (b"error sending request", "network or DNS connectivity failure"),
    (b"model_not_found", "requested model unavailable or not authorized"),
    (b"Unknown model", "requested model unavailable or not authorized"),
    (b"The model does not exist", "requested model unavailable or not authorized"),
    (b"Unauthorized: not authorized to access this model", "requested model unavailable or not authorized"),
    (b"Authentication failed", "authentication failed"),
    (b"Rate limit exceeded", "usage or rate limit reached"),
    (b"Unexpected failure", "unclassified CLI failure (diagnostics withheld)"),
])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_exit_errors_report_safe_failure_category(fake_cli, diagnostic, category, stream):
    output = {"stdout": b"", "stderr": b""}
    output[stream] = diagnostic + b" token=secret-token /private/secret-path"
    fake_cli(**output, status=2)
    with pytest.raises(transport.CLITransportError) as caught:
        asyncio.run(request())
    assert str(caught.value) == f"CLI exited with status 2: {category}."


@pytest.mark.parametrize("stdout", [b"not JSON secret-data", b"[]\n", b"\xff", b""])
def test_malformed_stream_is_rejected(fake_cli, stdout):
    fake_cli(stdout=stdout)
    with pytest.raises(transport.CLITransportError):
        asyncio.run(request())


def test_timeout_kills_process_group_and_releases_lock(fake_cli, monkeypatch):
    calls = fake_cli(codex_events(), delay=10)
    killed = []
    monkeypatch.setattr(transport.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(transport.CLITransportError, match="timed out"):
        asyncio.run(request(timeout=0.01))
    assert killed == [(123456, signal.SIGKILL)]
    assert not Path(calls[0]["options"]["cwd"]).exists()
    assert not transport._PROCESS_LOCK.locked()


def test_calls_are_serial_and_have_separate_directories(fake_cli):
    calls = fake_cli(codex_events(), delay=0.03)

    async def run_both():
        return await asyncio.gather(request(), request())

    results = asyncio.run(run_both())
    assert len(results) == 2
    assert len(calls) == 2
    assert calls[0]["options"]["cwd"] != calls[1]["options"]["cwd"]
    assert all(call["active_at_start"] == 1 for call in calls)


def test_no_completed_result_is_rejected(fake_cli):
    fake_cli(codex_events()[:-1])
    with pytest.raises(transport.CLITransportError, match="no completed"):
        asyncio.run(request())


def test_cancellation_kills_children_and_releases_lock(fake_cli, monkeypatch):
    fake_cli(codex_events(), delay=10)
    killed = []
    monkeypatch.setattr(transport.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    async def cancel_request():
        task = asyncio.create_task(request())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_request())
    assert killed == [(123456, signal.SIGKILL)]
    assert not transport._PROCESS_LOCK.locked()


def test_unexpected_exposed_tools_are_rejected_before_result_is_used(fake_cli):
    events = claude_events()
    events[0]["tools"].append("Read")
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="exposed unexpected tools"):
        asyncio.run(request("claude"))


def test_output_size_limit(fake_cli, monkeypatch):
    fake_cli(codex_events())
    monkeypatch.setattr(transport, "_MAX_OUTPUT_BYTES", 20)
    with pytest.raises(transport.CLITransportError, match="size limit"):
        asyncio.run(request())
