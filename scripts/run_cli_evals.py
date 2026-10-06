"""Run the core suite through logged-in CLIs, retaining native Inspect logs.

Start with --smoke to validate each model/CLI combination before the full run.
No model fallback or sample retry is performed: infrastructure errors must not
silently become benchmark scores or a different model.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# The three Anthropic models are specified by the briefing. The Codex model
# matches the owner's configured CLI model at the time this runner was added.
DEFAULT_MODELS = [
    "claude_cli/claude-sonnet-5",
    "claude_cli/claude-opus-5-5",
    "claude_cli/claude-haiku-4-5-20251001",
    "codex_cli/gpt-6-astra",
]


def source_files() -> list[Path]:
    paths = [ROOT / "pyproject.toml"]
    for directory in ("execbench", "scripts", "tests"):
        paths.extend((ROOT / directory).glob("*.py"))
    return sorted(paths)


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in source_files():
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def source_commit(*, require_clean: bool) -> str | None:
    """Published runs must identify a commit containing the exact evaluated sources."""
    if not shutil.which("git"):
        if require_clean:
            raise RuntimeError("Full evaluations require Git and a committed source tree")
        return None
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True,
    )
    if commit.returncode != 0:
        if require_clean:
            raise RuntimeError("Commit the benchmark sources before a full evaluation")
        return None
    if require_clean:
        paths = [path.relative_to(ROOT).as_posix() for path in source_files()]
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", *paths],
            cwd=ROOT, text=True, capture_output=True,
        )
        status = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--",
             "pyproject.toml", "execbench", "scripts", "tests"],
            cwd=ROOT, text=True, capture_output=True,
        )
        if tracked.returncode != 0 or status.returncode != 0 or status.stdout.strip():
            raise RuntimeError("Full evaluations require all benchmark sources tracked and unchanged from HEAD")
    return commit.stdout.strip()


def cli_version(executable: str) -> str:
    if not shutil.which(executable):
        raise RuntimeError(f"Required CLI not found: {executable}")
    result = subprocess.run(
        [executable, "--version"], text=True, capture_output=True, timeout=15, check=True,
    )
    return result.stdout.strip()


def validate_run(log, scenario_ids: list[str], epochs: int) -> list[dict]:
    if log.status != "success":
        raise RuntimeError(f"Evaluation ended with status {log.status}; inspect its log")
    samples = log.samples or []
    expected = Counter((sample_id, epoch) for sample_id in scenario_ids for epoch in range(1, epochs + 1))
    actual = Counter((sample.id, sample.epoch) for sample in samples)
    if actual != expected:
        raise RuntimeError("Log does not contain exactly the expected scenario/epoch pairs")
    limited = []
    for sample in samples:
        if sample.error or not sample.scores or "execution_scorer" not in sample.scores:
            raise RuntimeError(f"Sample {sample.id}, epoch {sample.epoch} failed or has no score")
        value = sample.scores["execution_scorer"].value
        score = value.get("score") if isinstance(value, dict) else None
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise RuntimeError(f"Sample {sample.id}, epoch {sample.epoch} has an invalid score")
        if sample.limit is not None:
            limit = sample.limit.model_dump(mode="json")
            if limit.get("type") != "message":
                raise RuntimeError(
                    f"Sample {sample.id}, epoch {sample.epoch} hit a non-message limit; run is incomplete"
                )
            limited.append({
                "sample_id": sample.id,
                "epoch": sample.epoch,
                "limit": limit,
            })
    return limited


def run(args: argparse.Namespace) -> None:
    # Import after argument parsing so --help also works before installation.
    from inspect_ai import eval as inspect_eval
    from inspect_ai.model import get_model

    import execbench._registry  # noqa: F401: register the CLI providers
    from execbench.scenarios import SCENARIOS
    from execbench.task import execbench

    models = args.model or DEFAULT_MODELS
    if len(set(models)) != len(models):
        raise ValueError("Each model may appear only once in a run")
    for model in models:
        if model.split("/", 1)[0] not in ("claude_cli", "codex_cli") or "/" not in model or not model.split("/", 1)[1]:
            raise ValueError(f"Expected claude_cli/MODEL or codex_cli/MODEL, got {model}")
    versions = {
        provider: cli_version(provider.removesuffix("_cli"))
        for provider in sorted({model.split("/", 1)[0] for model in models})
    }
    inspect_version = importlib.metadata.version("inspect-ai")
    fingerprint = source_fingerprint()
    git_commit = source_commit(require_clean=not args.smoke)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_dir = ROOT / "logs" / ("cli-smoke" if args.smoke else "cli-core") / stamp
    log_dir.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("INSPECT_TRACE_FILE", str(log_dir / "inspect-trace.log"))
    epochs = 1 if args.smoke else 3
    scenario_ids = ["deep_calm"] if args.smoke else [s.id for s in SCENARIOS]
    manifest = {
        "status": "running",
        "track": "cli",
        "smoke_only": args.smoke,
        "started_at": stamp,
        "inspect_version": inspect_version,
        "python_version": sys.version.split()[0],
        "cli_versions": versions,
        "source_sha256": fingerprint,
        "git_commit": git_commit,
        "models": models,
        "epochs": epochs,
        "scenario_ids": scenario_ids,
        "message_limit": 160,
        "parallel_tool_calls": False,
        "call_timeout_seconds": args.call_timeout,
        "sample_time_limit_seconds": args.sample_time_limit,
        "reasoning_effort": "CLI default (not overridden)",
        "runs": [],
    }
    manifest_path = log_dir / "run-manifest.json"

    def save() -> None:
        text = json.dumps(manifest, indent=2, allow_nan=False) + "\n"
        pending = manifest_path.with_suffix(".tmp")
        pending.write_text(text)
        pending.replace(manifest_path)

    def check_environment(provider: str) -> None:
        if source_fingerprint() != fingerprint:
            raise RuntimeError("Source changed during evaluation; start a new run")
        if not args.smoke and source_commit(require_clean=True) != git_commit:
            raise RuntimeError("Git commit changed during evaluation; start a new run")
        if importlib.metadata.version("inspect-ai") != inspect_version:
            raise RuntimeError("Inspect version changed during evaluation")
        if cli_version(provider.removesuffix("_cli")) != versions[provider]:
            raise RuntimeError("CLI version changed during evaluation")

    save()
    print(f"Inspect {inspect_version}; logs: {log_dir}", flush=True)
    try:
        for model_name in models:
            provider = model_name.split("/", 1)[0]
            check_environment(provider)
            print(f"Running {model_name}: {len(scenario_ids)} scenarios × {epochs} epochs", flush=True)
            model = get_model(
                model_name,
                call_timeout=args.call_timeout,
                workspace_root=str(ROOT.parent),
            )
            [log] = inspect_eval(
                execbench(),
                model=model,
                epochs=epochs,
                sample_id=scenario_ids,
                log_dir=str(log_dir),
                log_format="json",
                log_model_api=True,
                log_realtime=False,
                max_samples=1,
                max_connections=1,
                max_retries=0,
                fail_on_error=True,
                time_limit=args.sample_time_limit,
                metadata={
                    "track": "cli", "source_sha256": fingerprint,
                    "cli_version": versions[provider], "smoke_only": args.smoke,
                },
            )
            check_environment(provider)
            limited = validate_run(log, scenario_ids, epochs)
            scores = [sample.scores["execution_scorer"].value["score"] for sample in log.samples]
            result = {
                "model": model_name,
                "status": log.status,
                "samples": len(scores),
                "mean_score": statistics.mean(scores),
                "limited_samples": limited,
                "log_location": str(log.location),
            }
            manifest["runs"].append(result)
            save()
            print(f"{model_name}: mean score {result['mean_score']:.4f}; limited samples {len(limited)}", flush=True)
        manifest["status"] = "success"
    except BaseException:
        manifest["status"] = "incomplete"
        raise
    finally:
        save()
    print("Smoke check completed." if args.smoke else "Core evaluation completed: 18 samples per model.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", help="CLI provider/model; repeat for multiple models")
    parser.add_argument("--smoke", action="store_true", help="Only deep_calm, one epoch per model")
    parser.add_argument("--call-timeout", type=float, default=180, help="Seconds allowed per CLI invocation")
    parser.add_argument("--sample-time-limit", type=int, default=1200, help="Seconds allowed per sample")
    args = parser.parse_args()
    if not math.isfinite(args.call_timeout) or args.call_timeout <= 0 or args.sample_time_limit <= 0:
        parser.error("Timeouts must be positive")
    try:
        run(args)
    except (RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Evaluation stopped: {exc}\n")


if __name__ == "__main__":
    main()
