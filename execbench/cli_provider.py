"""Inspect providers for authenticated CLI sessions.

Only transport changes: Inspect still owns the agent loop, market tools,
action log and scoring. CLI results belong to a separate evaluation track
because CLI prompts and structured-output handling can affect behaviour.
"""

from __future__ import annotations

from pathlib import Path
import math

from inspect_ai.model import (
    ChatMessage,
    GenerateConfig,
    ModelAPI,
    ModelCall,
    ModelOutput,
    ModelUsage,
)
from inspect_ai.tool import ToolChoice, ToolInfo

from execbench.cli_transport import request_decision


def visible_messages(messages: list[ChatMessage]) -> list[dict]:
    """Send conversation content, never sample metadata or opaque reasoning."""
    result = []
    for message in messages:
        raw = message.model_dump(mode="json", exclude_none=True)
        item = {"role": raw["role"], "content": message.text}
        for key in ("tool_calls", "tool_call_id", "function", "error"):
            if key in raw:
                item[key] = raw[key]
        if "tool_calls" in item:
            item["tool_calls"] = [
                {k: call[k] for k in ("id", "function", "arguments", "type") if k in call}
                for call in item["tool_calls"]
            ]
        result.append(item)
    return result


def model_usage(provider: str, usage: dict | None) -> ModelUsage:
    """Preserve actual token counts; CLI subscription usage has no API price."""
    if not isinstance(usage, dict):
        raise ValueError("CLI did not report token usage")

    def count(name: str, *, required: bool = False) -> int:
        value = usage.get(name, None if required else 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"CLI did not report a valid {name} count")
        return value

    inputs = count("input_tokens", required=True)
    outputs = count("output_tokens", required=True)
    if provider == "codex":
        cached = count("cached_input_tokens")
        if cached > inputs:
            raise ValueError("Codex cached tokens exceed total input tokens")
        inputs -= cached
        cache_write = 0
    else:
        cached = count("cache_read_input_tokens")
        cache_write = count("cache_creation_input_tokens")
    return ModelUsage(
        input_tokens=inputs,
        output_tokens=outputs,
        input_tokens_cache_read=cached,
        input_tokens_cache_write=cache_write,
        total_tokens=inputs + cached + cache_write + outputs,
    )


class CLIModelAPI(ModelAPI):
    provider: str

    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_vars: list[str] | None = None,
        config: GenerateConfig | None = None,
        *,
        call_timeout: float = 180,
        workspace_root: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        if base_url or api_key or api_key_vars:
            raise ValueError("CLI providers use the CLI's own login; do not pass API credentials")
        if not math.isfinite(call_timeout) or call_timeout <= 0:
            raise ValueError("call_timeout must be positive")
        super().__init__(model_name, config=config or GenerateConfig())
        self.call_timeout = call_timeout
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[2])
        self.reasoning_effort = reasoning_effort

    def max_connections(self) -> int:
        return 1

    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> tuple[ModelOutput, ModelCall]:
        if config.parallel_tool_calls is True:
            raise ValueError("ExecBench CLI providers require sequential tool calls")
        # Inspect owns scheduling, timeouts, caching and conversation handling.
        # Reject model-generation controls the CLI adapter cannot forward rather
        # than recording settings that have no effect on the evaluated model.
        for field in (
            "temperature", "top_p", "top_k", "seed", "max_tokens", "stop_seqs",
            "best_of", "frequency_penalty", "presence_penalty", "logit_bias",
            "num_choices", "logprobs", "top_logprobs", "prompt_logprobs",
            "internal_tools", "cache_prompt", "verbosity", "effort",
            "reasoning_mode", "reasoning_tokens", "reasoning_summary",
            "response_schema", "extra_headers", "extra_body", "modalities", "fallback_models",
        ):
            if getattr(config, field, None) is not None:
                raise ValueError(f"The CLI adapter cannot enforce {field}; leave it unset")
        if config.batch not in (None, False):
            raise ValueError("The CLI adapter cannot enforce batch; leave it disabled")
        messages = visible_messages(input)
        tool_specs = [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        choice = tool_choice if isinstance(tool_choice, str) else tool_choice.model_dump(mode="json")
        effort = self.reasoning_effort or config.reasoning_effort
        response = await request_decision(
            self.provider,
            self.model_name,
            messages,
            tool_specs,
            choice,
            timeout=self.call_timeout,
            workspace_root=self.workspace_root,
            reasoning_effort=effort,
        )
        observed_model = response.get("observed_model")
        if observed_model is not None and observed_model != self.model_name:
            raise ValueError(
                "CLI reported a different model; use its exact model ID instead of an alias"
            )
        response["model_identity_verified"] = observed_model == self.model_name
        output = ModelOutput.for_tool_call(
            model=f"{self.provider}_cli/{self.model_name}",
            tool_name=response["action"],
            tool_arguments=response["arguments"],
            content=response["rationale"],
        )
        output.usage = model_usage(self.provider, response["usage"])
        call = ModelCall.create(
            request={
                "track": "cli",
                "provider": self.provider,
                "model": self.model_name,
                "reasoning_effort": effort or "CLI default",
                "messages": messages,
                "tools": tool_specs,
                "tool_choice": choice,
                "timeout_seconds": self.call_timeout,
            },
            response=response,
        )
        return output, call


class CodexCLI(CLIModelAPI):
    provider = "codex"


class ClaudeCLI(CLIModelAPI):
    provider = "claude"
