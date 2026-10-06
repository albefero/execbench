"""Validate completed CLI runs and publish deterministic core results.

Usage: python scripts/results_table.py PATH_TO_COMPLETED_MANIFEST [MORE_MANIFESTS...]
Only completed core manifests are accepted; smoke checks never enter the table.
Choose the successful attempts explicitly; a glob can include interrupted runs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from inspect_ai.log import read_eval_log

from execbench.baselines import run_policy
from execbench.scenarios import BY_ID, SCENARIOS
from execbench.scoring import evaluate

CORE_IDS = [scenario.id for scenario in SCENARIOS]
EPOCHS = 3
PROVENANCE_FIELDS = (
    "git_commit", "source_sha256", "inspect_version", "python_version",
    "epochs", "message_limit", "parallel_tool_calls", "call_timeout_seconds",
    "sample_time_limit_seconds", "reasoning_effort",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def number(value, label: str, minimum=None, maximum=None) -> float:
    require(
        not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value),
        f"{label} must be a finite number",
    )
    require(minimum is None or value >= minimum, f"{label} is below its valid range")
    require(maximum is None or value <= maximum, f"{label} is above its valid range")
    return float(value)


def _manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "Manifest must be a JSON object")
    require(manifest.get("status") == "success", "Manifest is not a successful completed run")
    require(manifest.get("track") == "cli" and manifest.get("smoke_only") is False,
            "Only core CLI manifests are accepted; smoke checks are excluded")
    require(manifest.get("epochs") == EPOCHS, "Core results require three epochs")
    require(Counter(manifest.get("scenario_ids", [])) == Counter(CORE_IDS),
            "Manifest does not contain exactly the six core scenarios")
    require(manifest.get("message_limit") == 160 and manifest.get("parallel_tool_calls") is False,
            "Core results require the unchanged 160-message limit and sequential tools")
    require(all(key in manifest for key in PROVENANCE_FIELDS), "Manifest provenance is incomplete")
    for key, pattern in (("git_commit", r"[0-9a-f]{40}(?:[0-9a-f]{24})?"),
                         ("source_sha256", r"[0-9a-f]{64}")):
        require(isinstance(manifest[key], str) and re.fullmatch(pattern, manifest[key]) is not None,
                f"Manifest has no valid {key}")
    for key in ("inspect_version", "python_version", "reasoning_effort", "started_at"):
        require(isinstance(manifest.get(key), str) and bool(manifest[key]), f"Missing {key}")
    for key in ("call_timeout_seconds", "sample_time_limit_seconds"):
        require(number(manifest[key], key) > 0, f"{key} must be positive")
    models = manifest.get("models")
    require(isinstance(models, list) and bool(models) and all(isinstance(m, str) for m in models),
            "Manifest must name at least one model")
    require(len(set(models)) == len(models), "Manifest contains duplicate models")
    require(all(re.fullmatch(r"(?:claude_cli|codex_cli)/.+", model) for model in models),
            "Manifest contains a non-CLI model")
    runs = manifest.get("runs")
    require(isinstance(runs, list) and all(isinstance(run, dict) for run in runs),
            "Manifest runs must be a list of objects")
    require(Counter(run.get("model") for run in runs) == Counter(models),
            "Manifest model entries are missing or duplicated")
    versions = manifest.get("cli_versions")
    require(isinstance(versions, dict), "Manifest CLI versions are missing")
    for model in models:
        version = versions.get(model.split("/", 1)[0])
        require(isinstance(version, str) and bool(version), "Manifest CLI version is missing")
    return manifest


def _sample_values(sample) -> dict:
    label = f"Sample {sample.id}, epoch {sample.epoch}"
    require(sample.error is None and sample.invalidation is None, f"{label} failed or was invalidated")
    require(not sample.error_retries and not sample.model_fallbacks, f"{label} retried or changed model")
    require(sample.scores is not None and "execution_scorer" in sample.scores, f"{label} has no execution score")
    score = sample.scores["execution_scorer"]
    require(isinstance(score.value, dict), f"{label} has malformed metrics")
    value = score.value
    result = {
        "score": number(value.get("score"), f"{label} score", 0, 1),
        "completion": number(value.get("completion"), f"{label} completion", 0, 1),
        "violations": number(value.get("violations"), f"{label} violations", 0),
    }
    require(result["violations"].is_integer(), f"{label} has fractional violations")
    try:
        explanation = json.loads(score.explanation)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(f"{label} has no valid scoring explanation") from None
    require(isinstance(explanation, dict), f"{label} has no metric explanation")
    # The scorer's numeric zero placeholders cannot distinguish no fills from
    # genuinely zero shortfall. Its explanation preserves the economic nulls.
    for key in ("shortfall_bps", "vs_twap_bps"):
        require(key in explanation, f"{label} explanation is missing {key}")
        result[key] = None if explanation[key] is None else number(explanation[key], f"{label} {key}")
        expected = 0.0 if result[key] is None else result[key]
        require(value.get(key) == expected, f"{label} score and explanation disagree on {key}")
    for key in ("score", "completion", "violations"):
        require(explanation.get(key) == result[key], f"{label} score and explanation disagree on {key}")
    if result["shortfall_bps"] is None:
        require(result["vs_twap_bps"] is None and result["completion"] == result["score"] == 0,
                f"{label} has inconsistent no-fill metrics")
    else:
        require(result["vs_twap_bps"] is not None, f"{label} is missing its TWAP comparison")
    require(sample.metadata is not None and sample.metadata.get("scenario") == BY_ID[sample.id].to_dict(),
            f"{label} parameters differ from the core scenario")
    return result


def _model_result(run: dict, manifest: dict, manifest_path: Path) -> dict:
    model = run["model"]
    require(run.get("status") == "success" and run.get("samples") == 18,
            f"{model} is not a completed 18-sample run")
    require(isinstance(run.get("log_location"), str) and bool(run["log_location"]),
            f"{model} has no log location")
    path = Path(run["log_location"])
    if not path.is_absolute():
        path = manifest_path.parent / path
    log = read_eval_log(path)
    require(log.status == "success" and log.error is None and not log.invalidated,
            f"{model} log failed or was invalidated")
    require(log.eval.model == model and log.eval.task in {"execbench", "execbench/execbench"},
            f"{model} log names a different model or task")
    require(log.eval.packages.get("inspect_ai") == manifest["inspect_version"], f"{model} Inspect version disagrees with manifest")
    metadata = log.eval.metadata or {}
    provider = model.split("/", 1)[0]
    expected_metadata = {
        "track": "cli", "source_sha256": manifest["source_sha256"],
        "smoke_only": False, "cli_version": manifest["cli_versions"][provider],
    }
    require(all(metadata.get(key) == value for key, value in expected_metadata.items()),
            f"{model} log provenance disagrees with manifest")
    require(log.eval.config.epochs == EPOCHS and log.eval.config.message_limit == 160,
            f"{model} log changed epochs or message limit")
    require(log.eval.config.time_limit == manifest["sample_time_limit_seconds"],
            f"{model} log changed sample time limit")
    require(log.plan is not None and log.plan.config.parallel_tool_calls is False,
            f"{model} log does not enforce sequential tools")
    samples = log.samples or []
    expected_pairs = Counter((scenario, epoch) for scenario in CORE_IDS for epoch in range(1, EPOCHS + 1))
    require(Counter((sample.id, sample.epoch) for sample in samples) == expected_pairs,
            f"{model} log must contain exactly the 18 scenario/epoch pairs")
    samples = sorted(samples, key=lambda sample: (CORE_IDS.index(sample.id), sample.epoch))
    values = [_sample_values(sample) for sample in samples]
    limited = []
    for sample in samples:
        if sample.limit is not None:
            require(sample.limit.type == "message" and sample.limit.limit == 160,
                    f"{model} reached a non-benchmark limit")
            limited.append({"sample_id": sample.id, "epoch": sample.epoch,
                            "limit": sample.limit.model_dump(mode="json")})
    require(isinstance(run.get("limited_samples"), list), f"{model} manifest omits limit records")
    ordered = lambda records: sorted(records, key=lambda item: (item["sample_id"], item["epoch"]))
    require(ordered(run["limited_samples"]) == ordered(limited), f"{model} manifest limit records disagree with log")
    by_scenario = {
        scenario: statistics.mean(value["score"] for sample, value in zip(samples, values) if sample.id == scenario)
        for scenario in CORE_IDS
    }
    by_epoch = {
        str(epoch): statistics.mean(value["score"] for sample, value in zip(samples, values) if sample.epoch == epoch)
        for epoch in range(1, EPOCHS + 1)
    }
    mean_score = statistics.mean(value["score"] for value in values)
    require(math.isclose(number(run.get("mean_score"), "Manifest mean score"), mean_score, abs_tol=1e-12),
            f"{model} manifest mean score disagrees with log")
    cost_means = {}
    for metric in ("shortfall_bps", "vs_twap_bps"):
        observed = [value[metric] for value in values if value[metric] is not None]
        cost_means[f"mean_{metric}"] = statistics.mean(observed) if observed else None
    return {
        "model": model, "n_samples": len(values), "mean_score": mean_score,
        "score_sem": statistics.stdev(by_scenario.values()) / math.sqrt(len(CORE_IDS)),
        "mean_completion": statistics.mean(value["completion"] for value in values),
        "mean_violations": statistics.mean(value["violations"] for value in values),
        **cost_means,
        "n_no_fills": sum(value["shortfall_bps"] is None for value in values),
        "n_message_limited": len(limited), "limited_samples": limited,
        "scenario_mean_scores": by_scenario,
        "epoch_mean_scores": by_epoch,
        "repeat_score_sem": statistics.stdev(by_epoch.values()) / math.sqrt(EPOCHS),
        "samples": [
            {"sample_id": sample.id, "epoch": sample.epoch, **value}
            for sample, value in zip(samples, values)
        ],
        "created_at": log.eval.created,
        "log_file": path.name, "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_summary(manifest_paths: list[Path]) -> dict:
    """Merge completed runs with one experimental protocol, irrespective of analysis HEAD."""
    require(bool(manifest_paths), "At least one completed manifest is required")
    common = None
    versions = {}
    models = {}
    for path in manifest_paths:
        path = Path(path)
        manifest = _manifest(path)
        provenance = {key: manifest[key] for key in PROVENANCE_FIELDS}
        if common is None:
            common = provenance
        require(provenance == common, "Manifests have inconsistent source/version/protocol provenance")
        for provider, version in manifest["cli_versions"].items():
            require(provider not in versions or versions[provider] == version,
                    "Manifests disagree on a CLI version")
            versions[provider] = version
        for run in manifest["runs"]:
            require(run["model"] not in models, "A model appears in multiple manifests; select one complete run")
            models[run["model"]] = _model_result(run, manifest, path)
    references = {
        policy: {scenario.id: evaluate(scenario, run_policy(scenario, policy))["score"] for scenario in SCENARIOS}
        for policy in ("twap", "dump")
    }
    return {
        "schema_version": 1, "track": "cli", "provenance": {**common, "cli_versions": versions},
        "scenario_ids": CORE_IDS,
        "aggregation": {
            "score_sem": "Descriptive SEM across six fixed-scenario means: sample standard deviation / sqrt(6).",
            "repeat_score_sem": "SEM of three whole-suite epoch means: sample standard deviation / sqrt(3); only three repeats.",
            "cost_means": "Only samples with fills; no-fill zero placeholders are excluded.",
            "interpretation": "Six fixed scenarios repeated three times; across-suite dispersion, not a confidence interval for unseen markets.",
        },
        "models": [models[model] for model in sorted(models)],
        "reference_scores": references,
    }


def markdown_table(summary: dict) -> str:
    def display(value):
        return "—" if value is None else f"{value:.2f}"

    def escape(value):
        return value.replace("|", "\\|").replace("\n", " ")

    rows = [
        "# Core model results", "",
        "CLI track · six scenarios × three executions per model.", "",
        "| Model | Score ± repeat SEM | Completion | IS (bps) | vs TWAP (bps) | Violations | No fills | Message-limited |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in summary["models"]:
        rows.append(
            f"| {escape(model['model'])} | {model['mean_score']:.3f} ± {model['repeat_score_sem']:.3f} "
            f"| {model['mean_completion']:.1%} | {display(model['mean_shortfall_bps'])} "
            f"| {display(model['mean_vs_twap_bps'])} | {model['mean_violations']:.2f} "
            f"| {model['n_no_fills']}/18 | {model['n_message_limited']}/18 |"
        )
    rows += ["", "Repeat SEM is the sample standard deviation of three whole-suite epoch means divided by √3; only three repeats are available.",
             "It describes repeat variability on these fixed scenarios, not uncertainty about unseen markets. Zero SEM means no variation was observed in those repeats.",
             "The JSON also retains the three epoch means and the separate across-scenario SEM, which describes heterogeneity across the six fixed cases.",
             "Model labels are requested CLI identifiers. A requested Codex ID is not independently verified when its response omits `observed_model`.",
             "Cost means exclude no-fill samples; their zero scores remain included.",
             "Positive vs TWAP means cheaper execution than TWAP. Violations are the mean count per execution.", "",
             "| Model | " + " | ".join(summary["scenario_ids"]) + " |",
             "|---|" + "---:|" * len(summary["scenario_ids"])]
    for model in summary["models"]:
        rows.append("| " + escape(model["model"]) + " | " + " | ".join(
            f"{model['scenario_mean_scores'][scenario]:.3f}" for scenario in summary["scenario_ids"]
        ) + " |")
    provenance = summary["provenance"]
    dates = ", ".join(sorted({model["created_at"][:10] for model in summary["models"]}))
    rows += ["", f"Run dates (UTC): {dates}. Inspect {provenance['inspect_version']}.",
             f"Evaluated commit: `{provenance['git_commit']}`.", "",
             "![Mean score by scenario, with scripted TWAP and dump references](scores_by_scenario.png)", ""]
    return "\n".join(rows)


def write_chart(summary: dict, path: Path) -> None:
    """Save a deterministic raster figure with no generated timestamps."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = [(model["model"], model["scenario_mean_scores"]) for model in summary["models"]]
    series += [("TWAP (scripted)", summary["reference_scores"]["twap"]),
               ("Dump (scripted)", summary["reference_scores"]["dump"])]
    with plt.rc_context({**plt.rcParamsDefault, "font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#B8C2CC", "text.color": "#172B3A"}):
        fig, ax = plt.subplots(figsize=(13, 5.7), layout="constrained")
        width = 0.82 / len(series)
        palette = ["#176B87", "#327D60", "#7963A8", "#C9783C", "#4D82B8", "#A65F78"]
        for index, (label, scores) in enumerate(series):
            reference = index >= len(summary["models"])
            color = ("#98A3AF" if index == len(series) - 2 else "#D5DCE2") if reference else palette[index % len(palette)]
            positions = [group - 0.41 + width * (index + 0.5) for group in range(len(CORE_IDS))]
            ax.bar(positions, [scores[s] for s in CORE_IDS], width=width * 0.9, label=label,
                   color=color, edgecolor="white", linewidth=0.5, hatch="///" if reference else None)
        ax.set_xticks(range(len(CORE_IDS)), [s.replace("_", " ").capitalize() for s in CORE_IDS])
        ax.set_ylim(0, 1.06)
        ax.set_ylabel("Mean execution score")
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#E7ECF0", linewidth=0.8)
        ax.set_title("Execution quality across six fixed scenarios", loc="left", fontsize=17, weight="bold", pad=16)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=min(3, len(series)), frameon=False, fontsize=9)
        fig.suptitle("CLI track · three executions per scenario · higher is better", x=0.02, ha="left", fontsize=10)
        fig.savefig(path, dpi=160, facecolor="white", metadata={"Software": "ExecBench results_table.py"})
        plt.close(fig)


def write_results(summary: dict, output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    table = markdown_table(summary)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "results.md").write_text(table, encoding="utf-8")
    write_chart(summary, output_dir / "scores_by_scenario.png")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path, help="Successful core run-manifest.json files")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    try:
        summary = build_summary(args.manifests)
        print(write_results(summary, args.output_dir), end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Results not generated: {exc}\n")


if __name__ == "__main__":
    main()
