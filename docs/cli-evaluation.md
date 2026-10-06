# CLI evaluation protocol

ExecBench can evaluate decisions produced by logged-in Claude Code and Codex
CLI sessions. The providers are named `claude_cli` and `codex_cli` so that this
track cannot be mistaken for a direct Anthropic or OpenAI API evaluation.

## What is being measured

Inspect retains the existing ReAct loop. For each decision, the adapter sends
the visible conversation and the benchmark tool schemas to a fresh CLI
process. It requests one structured action and a brief explanation. Inspect
then executes that action using the real benchmark tools. The simulator is
still reconstructed from the per-sample action log, including during scoring.

The prompt labels benchmark schemas as `action_definitions`: they describe
actions for the external executor, not callable tools in the CLI session.
Claude uses only its `StructuredOutput` formatting tool to return the decision;
Inspect executes the selected benchmark action after the request completes.

The adapter never receives scenario metadata, future prices or reference
policy results. The supplied prompt contains the same task information as
the API track, plus the transport instructions needed to return an action as
JSON. This is a methodological difference, not a claim of API equivalence.

The explanation is model-produced text. It is useful evidence of what the
agent said, not a measurement of its internal reasoning.

## Preconditions

- Install ExecBench with `pip install -e '.[dev]'` or the equivalent `uv pip`
  command inside a virtual environment.
- Install the CLIs and log in through their normal authentication flows.
- Use a terminal with network access and normal access to each CLI's own
  runtime directories. A CLI may need writable runtime state even when its
  model-generated commands use a read-only sandbox.
- Keep credentials in their existing CLI stores. Never copy them into this
  repository, a prompt, an environment snapshot or an evaluation log.

## Running

Start with a smoke check. It evaluates `deep_calm` once per model and is kept
under `logs/cli-smoke/`; it is not part of the published core results.

```bash
python scripts/run_cli_evals.py --smoke
```

Then run the core experiment:

```bash
python scripts/run_cli_evals.py
```

Before the full experiment, commit the benchmark sources. Full runs require
`pyproject.toml` and the Python sources to be tracked, with no pending changes
under `execbench/`, `scripts/` or `tests/`. The same commit is required throughout
the run. Smoke checks can run before Git is initialised or changes are committed.

The default model list preserves the three Anthropic identifiers specified
in the briefing and uses the owner's configured Codex model, `gpt-6-astra`,
as the additional model. Availability must be established by the smoke
check. An unavailable model fails explicitly; there is no automatic fallback.
When a CLI reports its model identifier, it must exactly match the requested
identifier. Use the exact model ID rather than an alias that resolves to a
different name; a mismatch stops the run.
To choose a different list, repeat `--model`:

```bash
python scripts/run_cli_evals.py --smoke --model codex_cli/gpt-6-astra
```

The full experiment runs six scenarios and three epochs per model, with one
active sample and one model call at a time. Each call has a 180-second
timeout; each sample has a 1,200-second time limit. These limits are recorded
in the manifest and can be selected explicitly with `--call-timeout` and
`--sample-time-limit`. The benchmark's 160-message limit is unchanged.

Three epochs repeat the same six seeded markets. They measure variation
between model executions on those markets, not generalisation to 18
independent markets. A CLI can make multiple internal turns to format one
decision; its token usage and raw result are retained in the Inspect log.
Subscription usage must not be described as a zero-dollar API cost.

## Isolation and failed runs

Each invocation starts in a fresh temporary directory, without resuming
prior CLI sessions. Personalisation and native tools are restricted by the
CLI-specific launch options. Codex additionally receives a filesystem rule
denying the benchmark workspace. The transport rejects unexpected native
tool calls, non-finite arguments and malformed responses. It never
executes a tool requested outside the provided benchmark schema.

Codex distinguishes non-fatal `item.error` diagnostics from fatal top-level
`error` and `turn.failed` events. The adapter validates and retains diagnostic
items, while still requiring a completed turn and valid decision. Model-reroute
diagnostics are rejected so that a fallback cannot be attributed to the requested
model. This follows the [official CLI event mapping](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs).

Claude's `rate_limit_event` and `keep_alive` records are protocol notifications,
not tool calls. The adapter validates them and retains them in the raw response;
the final `result` event determines whether the call succeeded. A quota-state
notification alone does not invalidate an already successful response.
The same applies to documented `system` telemetry: `thinking_tokens`,
`thinking`, `status`, `turn_duration` and `notification`. These records do not
replace final usage counts or authorise tool activity or model changes.

A Claude Code 2.1.290 smoke attempt emitted `advance` through the native
`tool_use` channel. The adapter rejected the attempted call; it was not mapped
to an Inspect action. This is evidence of a protocol mismatch, not evidence
that the CLI executed `advance`. The clarified prompt explicitly separates
external actions from the `StructuredOutput` formatting call. Failed smoke
attempts produce no benchmark scores.

These controls require an end-to-end smoke test with the installed CLI
versions before publishing results. Unit tests of command construction do
not establish that a particular CLI build enforces every option.

A failed CLI call is an evaluation error, not a zero score. The runner stops
on errors rather than selecting a different model or retrying a failed
decision silently. A sample hitting a time limit or any limit other than the
benchmark's message limit makes the run incomplete; it cannot become a published
score for a completed run. Message-limited samples retain their scores and are
listed by scenario and epoch in the manifest. All limited samples remain in the
original Inspect log.

## Reproducibility and reporting

Every run records the Inspect version, Python version, CLI versions, requested
model identifiers, source SHA-256, Git commit (optional only for smoke checks),
scenario IDs, epochs and limits. Source, Git commit, Inspect and CLI versions are checked between
models. Preserve the original logs locally. Publish aggregate results only
after checking all expected `(scenario, epoch)` pairs and failure states.

Logs use Inspect's native JSON format. With Inspect 0.3.276, local validation
completed evaluation but stalled when loading samples from the default `.eval`
format. JSON preserves the full transcript and works with `read_eval_log`;
the runner does not parse or rewrite Inspect logs itself.

Model call records contain the actual CLI response, usage and observed model
identifier when the CLI provides it. The runner explicitly retains every raw
model call, including calls beyond Inspect's default sampling of API records.
It disables the realtime SQLite buffer; completed Inspect logs remain available
for review. The trace file defaults to the run's log directory unless
`INSPECT_TRACE_FILE` is already configured.

A missing observed identifier must not
be represented as independently verified; the adapter records
`model_identity_verified: false` in that case. Temperature, sampling seed and
maximum output tokens are not exposed uniformly by these CLIs; the adapter
rejects overrides it cannot enforce. Reasoning effort uses the CLI default
unless explicitly configured and recorded.

The scorer's historical zero placeholders for no-fill shortfall are not
economic observations. Results analysis must use the explanation's `null`
values to exclude those runs from cost means and report their count. Positive
`vs_twap_bps` means cheaper execution than TWAP.

References: [Inspect model providers](https://inspect.aisi.org.uk/extensions-model-api.html),
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Claude Code programmatic use](https://code.claude.com/docs/en/headless).
