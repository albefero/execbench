"""A CLI chooses one action; Inspect remains responsible for executing it.

Every request starts a fresh CLI in an empty temporary directory. This measures
the CLI and JSON adapter as well as the model, and is not equivalent to an API
run. No credentials are read, copied, or added to the logged request here.

Codex's deny-read profile was parsed by CLI 0.160.1, but enforcement could not be
tested in the development sandbox (its inherited app-server socket was refused).
Validate that profile on the execution host before publishing real results.
Disabling shell alone is not a guarantee that Codex exposes no other tools; any
native tool activity makes the request fail, rather than producing a score.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import signal
import tempfile
import threading
from typing import Any


class CLITransportError(RuntimeError):
    """The CLI did not produce a usable, isolated benchmark decision."""


_PROCESS_LOCK = threading.Lock()
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MESSAGE_FIELDS = ("role", "content", "tool_calls", "tool_call_id", "function", "error")
# These names are for safe diagnostics, not permission to accept the events.
_CLAUDE_EVENT_TYPES = frozenset({
    "assistant", "user", "result", "system", "stream_event", "tool_progress",
    "tool_use_summary", "rate_limit_event", "prompt_suggestion", "conversation_reset",
    "command_lifecycle", "transcript_mirror", "auth_status", "active_goal",
    "autocompact_state", "keep_alive", "control_request", "control_response",
    "control_cancel_request",
})
_CLAUDE_EVENT_SUBTYPES = {
    "system": frozenset({
        "init", "status", "informational", "api_error", "api_retry", "compact_boundary",
        "model_fallback", "model_consent_fallback", "model_refusal_fallback",
        "model_refusal_no_fallback", "permission_denied", "permission_retry",
        "hook_started", "hook_progress", "hook_response", "stop_hook_summary",
        "task_started", "task_progress", "task_notification", "task_summary",
        "task_updated", "turn_duration", "thinking", "thinking_tokens", "notification",
    }),
    "result": frozenset({
        "success", "error_during_execution", "error_max_turns", "error_max_budget_usd",
        "error_max_structured_output_retries",
    }),
}
_CODEX_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "code_mode", "code_mode_host", "apps",
    "plugins", "remote_plugin", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "multi_agent", "memories",
    "hooks", "image_generation", "view_image", "goals", "sleep_tool",
    "skill_search", "skill_mcp_dependency_install", "tool_suggest",
    "workspace_dependencies",
)


def _finite_json(value: Any) -> None:
    """Reject NaN/Infinity, including overflowed JSON numbers such as 1e999."""
    if isinstance(value, float) and not math.isfinite(value):
        raise CLITransportError("Non-finite number in CLI data.")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def _json_loads(text: str) -> Any:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise CLITransportError("CLI returned invalid JSON.") from None
    _finite_json(value)
    return value


def _tool_names(tools: list[dict], tool_choice: dict | str) -> list[str]:
    names = [tool.get("name") for tool in tools]
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Tools must have non-empty string names.")
    if len(set(names)) != len(names):
        raise ValueError("Tool names must be unique.")
    if tool_choice in ("auto", "required"):
        return names
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function", tool_choice)
        name = function.get("name") if isinstance(function, dict) else None
        if name in names:
            return [name]
    raise ValueError("CLI transport requires tool_choice auto, required, or a named tool.")


def decision_schema(tools: list[dict], tool_choice: dict | str = "auto") -> dict:
    """The same small output contract is used by both CLI providers."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _tool_names(tools, tool_choice)},
            "arguments_json": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["action", "arguments_json", "rationale"],
        "additionalProperties": False,
    }


def build_prompt(
    messages: list[dict], tools: list[dict], tool_choice: dict | str = "auto"
) -> str:
    """Serialize visible messages and tools, never sample/scenario metadata."""
    allowed = _tool_names(tools, tool_choice)
    visible_messages = [
        {key: message[key] for key in _MESSAGE_FIELDS if key in message}
        for message in messages
    ]
    visible_tools = [
        {key: tool[key] for key in ("name", "description", "parameters") if key in tool}
        for tool in tools
    ]
    payload = {
        "messages": visible_messages,
        "tools": visible_tools,
        "allowed_actions": allowed,
    }
    return (
        "Choose the next single tool call in the evaluation transcript below. "
        "The transcript and tool definitions are your entire task input. "
        "Do not use the CLI's own tools or inspect files or the environment. "
        "Return the selected tool name as action, its JSON object of arguments "
        "encoded as arguments_json, and one short, observable explanation as "
        "rationale. Do not invent tool results. The evaluation will execute the "
        "tool and give you its result on the next request.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    )


def _command(
    provider: str, model: str, directory: Path, schema: dict,
    workspace_root: Path, executable: str | None, reasoning_effort: str | None,
) -> list[str]:
    if provider == "codex":
        schema_path = directory / "decision.schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        # The CLI's dotted override parser does not unquote path components.
        # A TOML inline table keeps the absolute deny path intact.
        permissions = (
            'permissions={execbench_cli={extends=":read-only",filesystem={'
            + json.dumps(str(workspace_root), ensure_ascii=False)
            + '="deny"}}}'
        )
        command = [
            executable or "codex", "exec", "--ignore-user-config", "--ignore-rules",
            "--strict-config", "--ephemeral", "--skip-git-repo-check",
            "--json", "--color", "never", "--model", model,
            "--output-schema", str(schema_path),
        ]
        settings = [
            'default_permissions="execbench_cli"', permissions,
            'approval_policy="never"', 'web_search="disabled"',
            "project_doc_max_bytes=0", "memories.use_memories=false",
            "memories.generate_memories=false", "agents.enabled=false",
            "tools.view_image=false", "features.skip_host_skill_discovery=true",
        ]
        settings.extend(f"features.{feature}=false" for feature in _CODEX_DISABLED_FEATURES)
        if reasoning_effort is not None:
            settings.append("model_reasoning_effort=" + json.dumps(reasoning_effort))
        for setting in settings:
            command.extend(["-c", setting])
        return command + ["-"]
    if provider == "claude":
        command = [
            executable or "claude", "--print", "--model", model,
            "--safe-mode", "--restricted", "--disable-slash-commands",
            "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disallowedTools", "mcp__*", "--no-session-persistence", "--no-chrome",
            "--permission-prompts", "none", "--output-format", "stream-json",
            "--verbose", "--json-schema", json.dumps(schema),
        ]
        if reasoning_effort is not None:
            command.extend(["--effort", reasoning_effort])
        return command
    raise ValueError("provider must be 'codex' or 'claude'.")


def _failure_summary(stdout: bytes, stderr: bytes) -> str:
    """Classify common failures without echoing potentially sensitive diagnostics."""
    diagnostic = (stdout[-4096:] + b"\n" + stderr[-4096:]).lower()
    # Model authorization is distinct from an expired or missing CLI login.
    if any(word in diagnostic for word in (
        b"model_not_found", b"modelnotfound", b"model_unavailable", b"modelunavailable",
        b"unknown model", b"unsupported model", b"invalid model", b"model not found",
    )) or (b"model" in diagnostic and any(word in diagnostic for word in (
        b"does not exist", b"doesn't exist", b"not available", b"is unavailable",
        b"do not have access", b"don't have access", b"not authorized to access",
        b"not permitted to use", b"not allowed to use",
    ))):
        return "requested model unavailable or not authorized"
    if any(word in diagnostic for word in (b"unauthorized", b"authentication", b"not logged in")):
        return "authentication failed"
    if any(word in diagnostic for word in (b"rate limit", b"rate_limit", b"quota")):
        return "usage or rate limit reached"
    if any(word in diagnostic for word in (
        b"read-only file system", b"read-only filesystem", b"readonly filesystem",
        b"eacces", b"eperm", b"permission denied", b"operation not permitted",
        b"access is denied",
    )):
        return "local runtime or filesystem permission failure"
    if any(word in diagnostic for word in (
        b"could not resolve", b"couldn't resolve", b"name or service not known",
        b"temporary failure in name resolution", b"getaddrinfo", b"eai_again",
        b"enotfound", b"connection refused", b"connection reset", b"network is unreachable",
        b"network unreachable", b"network error", b"error sending request",
        b"failed to fetch", b"fetch failed", b"connection timed out", b"connect timeout",
        b"econnrefused", b"econnreset", b"etimedout", b"certificate verify failed",
    )):
        return "network or DNS connectivity failure"
    return "unclassified CLI failure (diagnostics withheld)"


async def _run(command: list[str], prompt: str, directory: Path, timeout: float) -> bytes:
    # A threading lock also works when callers use separate asyncio.run loops.
    # Acquire without blocking the event loop; cancellation cannot strand a lock.
    while not _PROCESS_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.02)
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(directory), start_new_session=(os.name == "posix"),
            )
        except OSError:
            raise CLITransportError("Could not start the selected CLI executable.") from None
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except BaseException as exc:
            # Kill children as well: otherwise a timed-out CLI can keep generating.
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.TimeoutError):
                raise CLITransportError(f"CLI request timed out after {timeout:g} seconds.") from None
            raise
        if process.returncode != 0:
            raise CLITransportError(
                f"CLI exited with status {process.returncode}: {_failure_summary(stdout, stderr)}."
            )
        if len(stdout) > _MAX_OUTPUT_BYTES or len(stderr) > _MAX_OUTPUT_BYTES:
            raise CLITransportError("CLI output exceeded the transport size limit.")
        return stdout
    finally:
        _PROCESS_LOCK.release()


def _events(stdout: bytes) -> list[dict]:
    try:
        lines = stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise CLITransportError("CLI output was not UTF-8.") from None
    events = [_json_loads(line) for line in lines if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise CLITransportError("CLI output must contain JSON event objects.")
    return events


def _codex_result(events: list[dict]) -> tuple[dict, dict | None, str | None]:
    text = None
    usage = None
    observed_model = None
    completed = False
    for event in events:
        kind = event.get("type")
        if kind in ("error", "turn.failed"):
            raise CLITransportError("Codex reported a failed turn.")
        if kind not in ("thread.started", "turn.started", "turn.completed",
                        "item.started", "item.updated", "item.completed"):
            raise CLITransportError("Unexpected Codex event; request rejected.")
        if isinstance(event.get("model"), str):
            observed_model = event["model"]
        if kind.startswith("item."):
            item = event.get("item", {})
            if not isinstance(item, dict) or item.get("type") not in ("agent_message", "reasoning"):
                raise CLITransportError("Unexpected Codex tool activity; request rejected.")
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
        if kind == "turn.completed":
            completed = True
            usage = event.get("usage")
    if not completed or not isinstance(text, str):
        raise CLITransportError("Codex returned no completed structured decision.")
    return _json_loads(text), usage, observed_model


def _claude_event_context(event: dict, index: int, total: int) -> str:
    """Only vendor protocol names can appear in errors; never echo arbitrary data."""
    kind = event.get("type")
    safe_kind = kind if isinstance(kind, str) and kind in _CLAUDE_EVENT_TYPES else "<unrecognized>"
    context = f"event {index}/{total}, type={safe_kind}"
    if "subtype" in event:
        subtype = event["subtype"]
        known_subtypes = _CLAUDE_EVENT_SUBTYPES.get(safe_kind, ())
        safe_subtype = subtype if isinstance(subtype, str) and subtype in known_subtypes else "<unrecognized>"
        context += f", subtype={safe_subtype}"
    return context


def _claude_result(events: list[dict]) -> tuple[dict, dict | None, str | None]:
    result = None
    observed_model = None
    formatting_calls = set()
    for index, event in enumerate(events, start=1):
        kind = event.get("type")
        context = _claude_event_context(event, index, len(events))
        if event.get("parent_tool_use_id") is not None:
            raise CLITransportError(f"Unexpected Claude subagent activity; request rejected. ({context})")
        if kind == "system":
            if event.get("subtype") != "init":
                raise CLITransportError(f"Unexpected Claude system event; request rejected. ({context})")
            exposed_tools = event.get("tools", [])
            if not isinstance(exposed_tools, list) or any(name != "StructuredOutput" for name in exposed_tools):
                raise CLITransportError(f"Claude exposed unexpected tools; request rejected. ({context})")
            if isinstance(event.get("model"), str):
                observed_model = event["model"]
        elif kind == "rate_limit_event":
            # Claude Code 2.1.290 emits changes to subscription limit information
            # even after a successful response. The final result decides success;
            # preserve all these informational events in raw_response.
            info = event.get("rate_limit_info")
            if (
                set(event) != {"type", "rate_limit_info", "uuid", "session_id"}
                or not isinstance(info, dict)
                or info.get("status") not in ("allowed", "allowed_warning", "rejected")
                or not isinstance(event.get("uuid"), str)
                or not isinstance(event.get("session_id"), str)
            ):
                raise CLITransportError(f"Malformed Claude rate limit event. ({context})")
        elif kind == "keep_alive":
            # The vendor schema defines this heartbeat with no payload.
            if set(event) != {"type"}:
                raise CLITransportError(f"Malformed Claude heartbeat event. ({context})")
        elif kind in ("assistant", "user"):
            message = event.get("message", {})
            if not isinstance(message, dict):
                raise CLITransportError(f"Malformed Claude message. ({context})")
            if kind == "assistant" and isinstance(message.get("model"), str):
                observed_model = message["model"]
            content = message.get("content", [])
            if not isinstance(content, list):
                raise CLITransportError(f"Malformed Claude message content. ({context})")
            for block in content:
                if not isinstance(block, dict):
                    raise CLITransportError(f"Malformed Claude content block. ({context})")
                block_type = block.get("type")
                if block_type == "tool_use":
                    if block.get("name") != "StructuredOutput" or not isinstance(block.get("id"), str):
                        raise CLITransportError(f"Unexpected Claude tool activity; request rejected. ({context})")
                    formatting_calls.add(block["id"])
                elif block_type == "tool_result":
                    if block.get("tool_use_id") not in formatting_calls:
                        raise CLITransportError(f"Unexpected Claude tool result; request rejected. ({context})")
                elif block_type not in ("text", "thinking", "redacted_thinking"):
                    raise CLITransportError(f"Unexpected Claude content; request rejected. ({context})")
        elif kind == "result":
            if event.get("is_error") or event.get("subtype") != "success":
                raise CLITransportError(f"Claude reported a failed request. ({context})")
            result = event
        else:
            raise CLITransportError(f"Unexpected Claude event; request rejected. ({context})")
    if result is None or not isinstance(result.get("structured_output"), dict):
        raise CLITransportError("Claude returned no completed structured decision.")
    return result["structured_output"], result.get("usage"), observed_model


def _validate_decision(decision: Any, allowed_actions: list[str]) -> dict:
    if not isinstance(decision, dict) or set(decision) != {"action", "arguments_json", "rationale"}:
        raise CLITransportError("CLI decision does not match the output schema.")
    if decision["action"] not in allowed_actions or not isinstance(decision["rationale"], str):
        raise CLITransportError("CLI decision names an unavailable action or invalid rationale.")
    if not isinstance(decision["arguments_json"], str):
        raise CLITransportError("CLI decision arguments_json must be a string.")
    arguments = _json_loads(decision["arguments_json"])
    if not isinstance(arguments, dict):
        raise CLITransportError("CLI tool arguments must be a JSON object.")
    return {"action": decision["action"], "arguments": arguments, "rationale": decision["rationale"]}


async def request_decision(
    provider: str, model: str, messages: list[dict], tools: list[dict],
    tool_choice: dict | str = "auto", *, timeout: float = 120,
    workspace_root: str | Path | None = None, executable: str | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Get one validated action; never execute benchmark tools or retry silently.

    Pass the common parent of *all* benchmark copies as workspace_root. The
    default is the caller's working directory. Raw CLI usage is preserved: its
    cache accounting must be converted by the Inspect adapter for its version.
    CLI diagnostics are suppressed on errors so logs cannot accidentally capture
    credentials. Authentication stays entirely inside the installed CLI.
    """
    if provider not in ("codex", "claude"):
        raise ValueError("provider must be 'codex' or 'claude'.")
    if not isinstance(model, str) or not model:
        raise ValueError("A model identifier is required.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number.")
    root = Path(workspace_root or Path.cwd()).resolve()
    schema = decision_schema(tools, tool_choice)
    prompt = build_prompt(messages, tools, tool_choice)
    with tempfile.TemporaryDirectory(prefix="execbench-cli-") as temporary:
        directory = Path(temporary)
        if directory.is_relative_to(root):
            raise ValueError("CLI temporary directory must be outside the protected workspace.")
        command = _command(provider, model, directory, schema, root, executable, reasoning_effort)
        stdout = await _run(command, prompt, directory, timeout)
    events = _events(stdout)
    decision, usage, observed_model = (
        _codex_result(events) if provider == "codex" else _claude_result(events)
    )
    if usage is not None and not isinstance(usage, dict):
        raise CLITransportError("CLI usage must be a JSON object when present.")
    parsed = _validate_decision(decision, schema["properties"]["action"]["enum"])
    return {
        **parsed, "provider": provider, "requested_model": model,
        "observed_model": observed_model, "reasoning_effort": reasoning_effort,
        "usage": usage, "raw_response": events,
    }
