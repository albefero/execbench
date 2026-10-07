"""Exercise the CLI contract without invoking a model or reading credentials."""

import asyncio
import json
from pathlib import Path
import signal
import sys

import anyio
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


def claude_rate_limit_event(status="allowed_warning"):
    """Fixture matching the schema embedded in Claude Code 2.1.290."""
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status, "rateLimitType": "five_hour",
            "utilization": 0.85, "resetsAt": 1791259200,
            "unifiedWindows": {"five_hour": {"utilization": 0.85, "resetsAt": 1791259200}},
            "isUsingOverage": False,
        },
        "uuid": "12345678-1234-4234-8234-123456789abc",
        "session_id": "12345678-1234-4234-8234-123456789def",
    }


def claude_telemetry(subtype, **fields):
    return {
        "type": "system", "subtype": subtype,
        "uuid": "12345678-1234-4234-8234-123456789abc",
        "session_id": "12345678-1234-4234-8234-123456789def",
        **fields,
    }


def claude_api_retry(**fields):
    """Exact system/api_retry wire schema in Claude Code 2.1.290."""
    return claude_telemetry(
        "api_retry",
        **{"attempt": 1, "max_retries": 10, "retry_delay_ms": 500,
           "error_status": 529, "error": "overloaded", **fields},
    )


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

            def __init__(self):
                self.finished = asyncio.Event()
                self.stdin = Input()
                self.stdout = Output(self, encoded, is_stdout=True)
                self.stderr = Output(self, stderr, is_stdout=False)

            async def wait(self):
                await self.finished.wait()
                return self.returncode

            def kill(self):
                self.returncode = -signal.SIGKILL
                self.finished.set()

        class Input:
            def write(self, data):
                calls[-1]["stdin"] = data.decode()

            async def drain(self):
                await asyncio.sleep(0)

            def close(self):
                pass

        class Output:
            def __init__(self, process, data, *, is_stdout):
                self.process, self.data, self.is_stdout = process, data, is_stdout
                self.offset, self.started = 0, False

            async def read(self, size):
                nonlocal active
                if not self.started:
                    self.started = True
                    try:
                        await asyncio.sleep(delay)
                    finally:
                        if self.is_stdout:
                            active -= 1
                            self.process.returncode = status
                            self.process.finished.set()
                chunk = self.data[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

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
        workspace_root=Path(__file__).resolve().parents[1], **kwargs,
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
    assert f'"{Path(__file__).resolve().parents[1]}"="deny"' in profile
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


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_repeated_consistent_model_identity_is_preserved(fake_cli, provider):
    if provider == "codex":
        events = codex_events()
        for event in events:
            event["model"] = "requested-model"
    else:
        events = claude_events()
        events[0]["model"] = "requested-model"
        events[1]["message"]["model"] = "requested-model"
        events[-1]["model"] = "requested-model"
    fake_cli(events)
    result = asyncio.run(request(provider))
    assert result["observed_model"] == "requested-model"
    assert result["raw_response"] == events


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("initial_model", ["requested-model", "private-other-model-id"])
def test_mixed_model_response_cannot_hide_behind_final_requested_model(fake_cli, provider, initial_model):
    if provider == "codex":
        events = codex_events()
        events[0]["model"] = initial_model
        events.insert(2, {"type": "turn.started", "model": "private-other-model-id"})
        events[-1]["model"] = "requested-model"
    else:
        events = claude_events()
        events[0]["model"] = initial_model
        events.insert(1, {"type": "assistant", "message": {
            "model": "private-other-model-id", "content": [{"type": "text", "text": "Planning."}],
        }})
        events[2]["message"]["model"] = "requested-model"
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="inconsistent model identifiers") as caught:
        asyncio.run(request(provider))
    assert "private-other-model-id" not in str(caught.value)


def test_claude_final_envelope_cannot_override_assistant_model(fake_cli):
    events = claude_events()
    events[1]["message"]["model"] = events[0]["model"]
    events[-1]["model"] = "different-model"
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="inconsistent model identifiers"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("status", ["allowed", "allowed_warning", "rejected"])
def test_claude_informational_events_preserve_success_and_raw_response(fake_cli, status):
    events = claude_events()
    events.insert(1, claude_rate_limit_event(status))
    events.insert(0, {"type": "keep_alive"})
    events.append({"type": "keep_alive"})
    fake_cli(events)
    result = asyncio.run(request("claude"))
    assert result["action"] == "buy"
    assert result["usage"] == events[-2]["usage"]
    assert result["raw_response"] == events


def test_claude_informational_events_do_not_override_final_error(fake_cli):
    events = claude_events()
    events.insert(1, claude_rate_limit_event("rejected"))
    events[-1] = {
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "errors": ["secret-diagnostic"],
    }
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="failed request") as caught:
        asyncio.run(request("claude"))
    assert "event 5/5, type=result, subtype=error_during_execution" in str(caught.value)
    assert "secret" not in str(caught.value)


def test_claude_headless_telemetry_sequence_keeps_decision_and_billed_usage(fake_cli):
    base = claude_events()
    events = [
        base[0],
        claude_telemetry("thinking_tokens", estimated_tokens=2400, estimated_tokens_delta=2400),
        claude_telemetry("status", status="requesting"),
        claude_telemetry("thinking", content="Fixture display text."),
        claude_telemetry("notification", key="fixture", text="Fixture notice.", priority="low"),
        claude_rate_limit_event(),
        *base[1:3],
        claude_telemetry("status", status=None),
        base[-1],
        claude_telemetry(
            "turn_duration", duration_ms=1250, budget_tokens=15, budget_limit=1000,
            budget_nudges=0, message_count=4, pending_background_agent_count=0,
            pending_workflow_count=0,
        ),
        {"type": "keep_alive"},
    ]
    fake_cli(events)
    result = asyncio.run(request("claude"))
    assert result["action"] == "buy"
    assert result["usage"] == base[-1]["usage"]
    assert result["usage"]["output_tokens"] == 15
    assert result["raw_response"] == events


@pytest.mark.parametrize("error", [
    "authentication_failed", "oauth_org_not_allowed", "account_on_hold",
    "verification_required", "billing_error", "rate_limit", "overloaded",
    "invalid_request", "model_not_found", "server_error", "unknown",
    "max_output_tokens", "cloud_credential_error",
])
def test_claude_api_retry_preserves_success_usage_and_raw_notice(fake_cli, error):
    events = claude_events()
    events.insert(1, claude_api_retry(error=error))
    fake_cli(events)
    result = asyncio.run(request("claude"))
    assert result["action"] == "buy"
    assert result["usage"] == events[-1]["usage"]
    assert result["raw_response"] == events
    assert result["observed_model"] == "claude-model-snapshot"


def test_claude_api_retry_accepts_documented_optional_timeout_details(fake_cli):
    # The SDK uses int(), without nonnegative bounds, and uuid is a string alias.
    event = claude_api_retry(error_status=None, attempt=0, max_retries=-1,
                            uuid="sdk-string-alias", no_response={"waited_ms": 30000, "retry_wait_ms": 500})
    fake_cli([event, *claude_events()])
    assert asyncio.run(request("claude"))["raw_response"][0] == event


@pytest.mark.parametrize("field", [
    "attempt", "max_retries", "retry_delay_ms", "error_status", "error", "uuid", "session_id",
])
def test_claude_api_retry_requires_every_sdk_field(fake_cli, field):
    event = claude_api_retry()
    del event[field]
    fake_cli([event, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed or unsafe Claude telemetry"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("fields", [
    {"attempt": True}, {"attempt": 1.5}, {"max_retries": "10"}, {"retry_delay_ms": None},
    {"error_status": False}, {"error_status": 500.5}, {"error_status": "529"},
    {"error": "private-new-error"}, {"error": None}, {"uuid": 1}, {"session_id": None},
    {"no_response": None}, {"no_response": []}, {"no_response": {}},
    {"no_response": {"waited_ms": 1}},
    {"no_response": {"waited_ms": True, "retry_wait_ms": 10}},
    {"no_response": {"waited_ms": 1, "retry_wait_ms": "10"}},
    {"no_response": {"waited_ms": 1, "retry_wait_ms": 10, "private-extra": "secret"}},
    {"usage": {"input_tokens": 123}}, {"private-extra": "secret"},
    {"message": {"content": "secret"}}, {"model": "private-model"},
])
def test_claude_api_retry_rejects_malformed_or_undocumented_fields(fake_cli, fields):
    fake_cli([claude_api_retry(**fields), *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed or unsafe Claude telemetry") as caught:
        asyncio.run(request("claude"))
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_claude_api_retry_does_not_accept_nonfinite_json(fake_cli):
    fake_cli([claude_api_retry(retry_delay_ms=float("nan")), *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Non-finite"):
        asyncio.run(request("claude"))


def test_claude_api_retry_cannot_replace_final_success(fake_cli):
    fake_cli([claude_api_retry(), *claude_events()[:-1]])
    with pytest.raises(transport.CLITransportError, match="no completed"):
        asyncio.run(request("claude"))


def test_claude_api_retry_does_not_hide_final_failure(fake_cli):
    events = [claude_api_retry(), *claude_events()]
    events[-1] = {"type": "result", "subtype": "error_during_execution", "is_error": True}
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="failed request"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("activity", ["native_tool", "model_fallback", "changed_model"])
def test_claude_api_retry_does_not_authorize_tools_or_model_changes(fake_cli, activity):
    events = [claude_api_retry(), *claude_events()]
    if activity == "native_tool":
        events[2]["message"]["content"][0]["name"] = "Read"
    elif activity == "model_fallback":
        events.insert(2, {"type": "system", "subtype": "model_fallback"})
    else:
        events[2]["message"]["model"] = "different-model"
    fake_cli(events)
    with pytest.raises(transport.CLITransportError):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("event", [
    claude_telemetry("status", status="compacting", compact_result="success", permissionMode="default"),
    claude_telemetry("thinking_tokens", estimated_tokens=3, estimated_tokens_delta=1, user_message_uuid="fixture"),
    claude_telemetry("notification", key="fixture", text="Fixture notice.", priority="immediate", timeout_ms=100, color="gray"),
])
def test_claude_telemetry_optional_vendor_fields_are_accepted(fake_cli, event):
    fake_cli([event, *claude_events()])
    assert asyncio.run(request("claude"))["action"] == "buy"


@pytest.mark.parametrize("event", [
    claude_telemetry("thinking_tokens", estimated_tokens=3),
    claude_telemetry("thinking_tokens", estimated_tokens=True, estimated_tokens_delta=1),
    claude_telemetry("thinking_tokens", estimated_tokens="secret", estimated_tokens_delta=1),
    claude_telemetry("thinking", content={"secret": "value"}),
    claude_telemetry("turn_duration", duration_ms="secret"),
    claude_telemetry("turn_duration", duration_ms=1, pending_background_agent_count=1),
    claude_telemetry("turn_duration", duration_ms=1, pending_workflow_count=1),
    claude_telemetry("status", status="secret"),
    claude_telemetry("status", status=None, compact_result="secret"),
    claude_telemetry("status", status=None, permissionMode="secret"),
    claude_telemetry("notification", key="fixture", text="secret", priority="unknown"),
    claude_telemetry("thinking", content="secret", uuid=None),
    claude_telemetry("thinking", content="secret", tool_name="Bash"),
])
def test_claude_malformed_or_unsafe_telemetry_is_rejected(fake_cli, event):
    fake_cli([event, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="telemetry event") as caught:
        asyncio.run(request("claude"))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("subtype", [
    "model_fallback", "model_refusal_fallback", "hook_started", "hook_response",
    "stop_hook_summary", "task_started", "permission_retry", "informational",
    "api_error", "compact_boundary",
])
def test_claude_telemetry_does_not_allow_side_effects_or_recovery_events(fake_cli, subtype):
    fake_cli([claude_telemetry(subtype, content="secret"), *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Unexpected Claude system event") as caught:
        asyncio.run(request("claude"))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("info", [None, [], "secret-info", {}, {"status": "unknown"}, {"status": []}])
def test_claude_malformed_rate_limit_info_is_rejected(fake_cli, info):
    event = claude_rate_limit_event()
    event["rate_limit_info"] = info
    fake_cli([event, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed Claude rate limit event"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("mutation", [
    {"uuid": None}, {"session_id": []}, {"message": {"content": "secret"}},
])
def test_claude_malformed_rate_limit_envelope_is_rejected(fake_cli, mutation):
    event = {**claude_rate_limit_event(), **mutation}
    fake_cli([event, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed Claude rate limit event"):
        asyncio.run(request("claude"))


def test_claude_rate_limit_envelope_requires_vendor_fields(fake_cli):
    event = claude_rate_limit_event()
    del event["uuid"]
    fake_cli([event, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed Claude rate limit event"):
        asyncio.run(request("claude"))


def test_claude_heartbeat_with_payload_is_rejected(fake_cli):
    fake_cli([{"type": "keep_alive", "content": "secret-payload"}, *claude_events()])
    with pytest.raises(transport.CLITransportError, match="Malformed Claude heartbeat event") as caught:
        asyncio.run(request("claude"))
    assert "secret" not in str(caught.value)


def test_claude_informational_events_cannot_replace_completed_result(fake_cli):
    fake_cli([claude_rate_limit_event(), {"type": "keep_alive"}])
    with pytest.raises(transport.CLITransportError, match="no completed"):
        asyncio.run(request("claude"))


@pytest.mark.parametrize("kind", ["tool_progress", "tool_use_summary", "control_request"])
def test_claude_other_protocol_events_remain_rejected_with_safe_context(fake_cli, kind):
    events = claude_events()
    events.insert(1, {"type": kind, "tool_name": "secret-tool", "uuid": "secret-id"})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="Unexpected Claude event") as caught:
        asyncio.run(request("claude"))
    assert str(caught.value) == (
        f"Unexpected Claude event; request rejected. (event 2/5, type={kind})"
    )


@pytest.mark.parametrize("event,context", [
    ({"type": "secret-type", "subtype": "secret-subtype"},
     "type=<unrecognized>, subtype=<unrecognized>"),
    ({"type": ["secret-type"], "subtype": {"secret": "value"}},
     "type=<unrecognized>, subtype=<unrecognized>"),
    ({"type": "system", "subtype": "secret-subtype"},
     "type=system, subtype=<unrecognized>"),
    ({"type": "system", "subtype": "api_retry"},
     "type=system, subtype=api_retry"),
])
def test_claude_error_context_redacts_arbitrary_names_and_payloads(fake_cli, event, context):
    events = claude_events()
    events.insert(1, {**event, "content": "secret-content", "uuid": "secret-id"})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError) as caught:
        asyncio.run(request("claude"))
    assert f"(event 2/5, {context})" in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"])
def test_codex_native_tool_activity_is_rejected(fake_cli, item_type):
    events = codex_events()
    events.insert(2, {"type": "item.started", "item": {"type": item_type}})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="tool activity"):
        asyncio.run(request())


@pytest.mark.parametrize("event_type", ["item.started", "item.updated", "item.completed"])
def test_codex_nonfatal_diagnostic_preserves_completed_decision_and_raw_response(fake_cli, event_type):
    events = codex_events()
    events.insert(2, {
        "type": event_type,
        "item": {"id": "diagnostic-1", "type": "error", "message": "Recovered transient diagnostic."},
    })
    fake_cli(events)
    result = asyncio.run(request())
    assert result["action"] == "buy"
    assert result["usage"] == events[-1]["usage"]
    assert result["raw_response"] == events


@pytest.mark.parametrize("field", ["id", "message"])
@pytest.mark.parametrize("invalid_value", [None, 42, []])
def test_codex_nonfatal_diagnostic_rejects_malformed_fields(fake_cli, field, invalid_value):
    item = {"id": "diagnostic-1", "type": "error", "message": "private-diagnostic-message"}
    item[field] = invalid_value
    events = codex_events()
    events.insert(2, {"type": "item.completed", "item": item})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="Malformed Codex diagnostic") as caught:
        asyncio.run(request())
    assert "private-diagnostic-message" not in str(caught.value)


@pytest.mark.parametrize("field", ["id", "message"])
def test_codex_nonfatal_diagnostic_requires_id_and_message(fake_cli, field):
    item = {"id": "diagnostic-1", "type": "error", "message": "Diagnostic."}
    del item[field]
    events = codex_events()
    events.insert(2, {"type": "item.completed", "item": item})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="Malformed Codex diagnostic"):
        asyncio.run(request())


def test_codex_nonfatal_diagnostic_does_not_authorize_native_tool_activity(fake_cli):
    events = codex_events()
    events[2:2] = [
        {"type": "item.completed", "item": {
            "id": "diagnostic-1", "type": "error", "message": "Diagnostic.",
        }},
        {"type": "item.started", "item": {"id": "command-1", "type": "command_execution"}},
    ]
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="tool activity"):
        asyncio.run(request())


@pytest.mark.parametrize("fatal_type", ["error", "turn.failed"])
def test_codex_nonfatal_diagnostic_cannot_mask_fatal_error(fake_cli, fatal_type):
    events = codex_events()
    events.insert(2, {"type": "item.completed", "item": {
        "id": "diagnostic-1", "type": "error", "message": "Diagnostic.",
    }})
    events.append({"type": fatal_type, "message": "private-fatal-diagnostic"})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="failed turn") as caught:
        asyncio.run(request())
    assert "private-fatal-diagnostic" not in str(caught.value)


def test_codex_nonfatal_diagnostic_cannot_replace_final_completion(fake_cli):
    events = codex_events()[:-1]
    events.append({"type": "item.completed", "item": {
        "id": "diagnostic-1", "type": "error", "message": "Diagnostic.",
    }})
    fake_cli(events)
    with pytest.raises(transport.CLITransportError, match="no completed"):
        asyncio.run(request())


def test_codex_model_rerouting_diagnostic_is_fatal_despite_final_requested_identity(fake_cli):
    events = codex_events()
    events[0]["model"] = "requested-model"
    events.insert(2, {"type": "item.completed", "item": {
        "id": "diagnostic-1", "type": "error", "message": "model rerouted: private-fallback-model",
    }})
    events[-1]["model"] = "requested-model"
    fake_cli(events)
    with pytest.raises(transport.CLITransportError) as caught:
        asyncio.run(request())
    assert "private-fallback-model" not in str(caught.value)


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


def test_inspect_cancel_scope_cannot_interrupt_process_cleanup(fake_cli, monkeypatch):
    calls = fake_cli(codex_events(), delay=10)
    killed = []
    monkeypatch.setattr(transport.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    async def cancel_request():
        with anyio.move_on_after(0.01) as scope:
            await request()
        assert scope.cancel_called

    asyncio.run(cancel_request())
    assert killed == [(123456, signal.SIGKILL)]
    assert not transport._PROCESS_LOCK.locked()
    assert not Path(calls[0]["options"]["cwd"]).exists()


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


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_oversized_output_is_stopped_before_a_live_process_times_out(tmp_path, monkeypatch, stream):
    # A local Python fixture keeps running after excessive output. No CLI or
    # model is invoked. A post-communicate size check would instead time out.
    monkeypatch.setattr(transport, "_MAX_OUTPUT_BYTES", 1024)
    command = [sys.executable, "-c", (
        f"import sys,time; sys.{stream}.buffer.write(b'x'*131072); "
        f"sys.{stream}.flush(); time.sleep(30)"
    )]
    with pytest.raises(transport.CLITransportError, match="size limit"):
        asyncio.run(transport._run(command, "", tmp_path, timeout=2))
    assert not transport._PROCESS_LOCK.locked()

    # Cleanup must leave the transport usable for the following decision.
    output = asyncio.run(transport._run(
        [sys.executable, "-c", "print('ready')"], "", tmp_path, timeout=2,
    ))
    assert output == b"ready\n"
