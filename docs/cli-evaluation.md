# CLI evaluation protocol

ExecBench can evaluate decisions produced by logged-in Claude Code and Codex
CLI sessions. The providers are named `claude_cli` and `codex_cli` so that this
track cannot be mistaken for a direct Anthropic or OpenAI API evaluation.

## What is being measured

Inspect retains the existing ReAct loop. For each decision, the adapter sends
the visible conversation and the benchmark tool schemas to a fresh CLI
process. It requests one structured action and a brief explanation. Inspect
then validates the arguments and executes the action using the benchmark tools.
The simulator is reconstructed from the per-sample action log, including during
scoring. Only an accepted `advance()` call moves simulated time; waiting for a
CLI, network response or retry does not advance the market. Wall-clock delays
can still exhaust the execution time limits.

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

The CLI transport is validated on Linux with Python 3.11. Timeouts terminate
the process group on Linux. The current Windows fallback terminates only the
parent process, so equivalent cleanup is not established there.

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

Independent runner processes may evaluate different CLI providers concurrently.
The core experiment permits at most one Claude runner and one Codex runner at
a time, each with one active sample and one model call. Each model can have its
own completed manifest, so a later interruption cannot obscure an earlier
completed run. Their manifests must identify the same source commit, source
hash and Inspect version before their results can be combined.

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

A CLI invocation that exits with an error, times out or fails transport
validation is an evaluation error, not a zero score. The adapter does not
relaunch that invocation. Inspect uses `max_retries=0` and the runner stops on
sample errors; it does not automatically repeat failed samples.

A valid CLI response can still propose invalid benchmark arguments. Inspect
returns a tool parsing error to the agent before executing that action. The
agent may make a new decision using that feedback, within the original limits.
This is a new model call, not a repeated CLI invocation or a sample restart.
A rejected argument schema does not change the market or count as a trading
rule violation; validly formed orders that break market rules are recorded
and penalised by the simulator.

A CLI may also retry a transient HTTP request internally before returning its
final decision. Inspect's retry setting does not control those internal loops.
Claude's validated `system/api_retry` notifications remain in the raw log;
they do not authorise model fallbacks or native actions. Multiple notifications
can describe the same waiting attempt, so frame counts are not exact API
request counts and their reported delays must not be summed as elapsed time.
The 180-second process timeout includes internal backoff and retries.
A sample hitting a time limit or any limit other than the
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

The recorded Codex track did not return an observed model identifier. Its
label therefore identifies the requested model, and its calls record
`model_identity_verified: false`. When a CLI does return an identifier, the
adapter checks that it matches the request; this is a check of the CLI's own
report, not independent verification of the hosted backend.

Replay is deterministic for a fixed scenario and recorded action list: it
reconstructs market state, fills, rejections and scores. Repeating a hosted
model evaluation is different. The same source commit, CLI versions and model
identifiers do not guarantee the same responses. Temperature, sampling seed
and maximum output tokens are not exposed uniformly by these CLIs; the adapter
rejects overrides it cannot enforce. Hosted services can change independently
of the local software. Reasoning effort uses the CLI default unless explicitly
configured and recorded.

The scorer's historical zero placeholders for no-fill shortfall are not
economic observations. Results analysis must use the explanation's `null`
values to exclude those runs from cost means and report their count. Positive
`vs_twap_bps` means cheaper execution than TWAP.

### Analysing completed runs

Select successful core manifests explicitly; do not include smoke checks or
interrupted attempts. The script accepts multiple compatible manifests, with
one complete run per model. This experiment used a separate manifest for each
of its four models:

```bash
python scripts/results_table.py \
  logs/cli-core/HAIKU_RUN/run-manifest.json \
  logs/cli-core/OPUS_RUN/run-manifest.json \
  logs/cli-core/SONNET_RUN/run-manifest.json \
  logs/cli-core/CODEX_RUN/run-manifest.json
```

Replace the four run names with the completed directories. The script reads
Inspect logs through `read_eval_log`, checks the 18 scenario/epoch pairs for
each model, and verifies that source and protocol metadata agree. It writes
`results/results.md`, `results/summary.json` and a grouped-bar PNG. Re-running
it with the same logs and plotting environment produces identical bytes.

The table reports the mean score with the standard error of the three
whole-suite epoch means. With only three repeats, this is a limited estimate
of repeat variability on the same fixed markets. The JSON also preserves
the separate across-scenario SEM and per-sample metrics. These quantities
do not establish generalisation to other markets or statistically significant
differences between models.

The experimental commit is recorded independently of later documentation and
results commits. The v0.2 extension preserves the six core definitions, scoring
formula, and all 12 core reference-policy action lists and metrics. Reproducing
the historical protocol requires its recorded commit and Inspect/CLI versions,
not just the latest checkout; these identify the configuration without
promising identical hosted responses. Earlier smoke checks and interrupted
attempts are excluded from the published experiment.

References: [Inspect model providers](https://inspect.aisi.org.uk/extensions-model-api.html),
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Claude Code programmatic use](https://code.claude.com/docs/en/headless).
