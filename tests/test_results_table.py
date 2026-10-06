"""Synthetic logs exercise reporting; these fixtures are not model results."""

from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path
import statistics

import pytest
from inspect_ai.log import (
    EvalConfig, EvalDataset, EvalError, EvalLog, EvalPlan, EvalSample,
    EvalSampleLimit, EvalSpec, read_eval_log,
)
from inspect_ai.model import GenerateConfig
from inspect_ai.scorer import Score

from execbench.scenarios import BY_ID

_spec = spec_from_file_location("results_table", Path(__file__).resolve().parents[1] / "scripts" / "results_table.py")
reporter = module_from_spec(_spec)
_spec.loader.exec_module(reporter)


@pytest.fixture
def completed_run(tmp_path):
    """Serialize Inspect's typed schema, then use its real public log reader.

    Inspect 0.3.276's standalone writer stalls in this environment; its public
    EvalLog serializer avoids that unrelated filesystem path in synthetic tests.
    """
    def create(model="claude_cli/test-model", directory="run"):
        folder = tmp_path / directory
        folder.mkdir()
        manifest = {
            "status": "success", "track": "cli", "smoke_only": False,
            "started_at": "20261006T120000000000Z", "inspect_version": "0.3.276",
            "python_version": "3.11.15", "git_commit": "a" * 40, "source_sha256": "b" * 64,
            "models": [model], "epochs": 3, "scenario_ids": reporter.CORE_IDS,
            "message_limit": 160, "parallel_tool_calls": False,
            "call_timeout_seconds": 180, "sample_time_limit_seconds": 1200,
            "reasoning_effort": "CLI default (not overridden)",
            "cli_versions": {model.split("/", 1)[0]: "test-cli 1.0"},
        }
        samples = []
        for scenario_index, scenario_id in enumerate(reporter.CORE_IDS):
            for epoch in range(1, 4):
                metrics = {
                    "score": round((scenario_index + 1) / 10 + epoch / 100, 2),
                    "completion": 1.0, "shortfall_bps": scenario_index * 10 + epoch,
                    "vs_twap_bps": 5 - epoch, "violations": 0,
                }
                samples.append(EvalSample(
                    id=scenario_id, epoch=epoch, input="Synthetic test task", target="",
                    metadata={"scenario": BY_ID[scenario_id].to_dict()},
                    scores={"execution_scorer": Score(value=metrics, explanation=json.dumps(metrics))},
                ))
        log = EvalLog(
            status="success",
            eval=EvalSpec(
                created="2026-10-06T12:00:00+00:00", task="execbench/execbench", model=model,
                dataset=EvalDataset(samples=6, sample_ids=reporter.CORE_IDS),
                config=EvalConfig(epochs=3, message_limit=160, time_limit=1200),
                packages={"inspect_ai": "0.3.276"},
                metadata={"track": "cli", "smoke_only": False, "source_sha256": "b" * 64,
                          "cli_version": "test-cli 1.0"},
            ),
            plan=EvalPlan(config=GenerateConfig(parallel_tool_calls=False)), samples=samples,
        )
        log_path = folder / "synthetic.json"
        manifest["runs"] = [{
            "model": model, "status": "success", "samples": 18,
            "mean_score": statistics.mean(s.scores["execution_scorer"].value["score"] for s in samples),
            "limited_samples": [], "log_location": log_path.name,
        }]
        manifest_path = folder / "run-manifest.json"

        def save():
            log_path.write_text(log.model_dump_json(exclude_none=True), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        save()
        return manifest_path, manifest, log, save

    return create


def test_aggregation_uses_six_scenario_means_not_eighteen_independent_samples(completed_run):
    path, _, log, _ = completed_run()
    summary = reporter.build_summary([path])
    result = summary["models"][0]
    scenario_means = [0.12, 0.22, 0.32, 0.42, 0.52, 0.62]
    assert result["mean_score"] == pytest.approx(0.37)
    assert result["score_sem"] == pytest.approx(statistics.stdev(scenario_means) / math.sqrt(6))
    naive_sem = statistics.stdev(s.scores["execution_scorer"].value["score"] for s in log.samples) / math.sqrt(18)
    assert result["score_sem"] != pytest.approx(naive_sem)
    assert result["mean_completion"] == 1
    assert result["mean_vs_twap_bps"] == 3
    assert result["n_samples"] == 18
    assert result["n_no_fills"] == 0


def test_public_sample_metrics_are_complete_ordered_and_exclude_private_fields(completed_run):
    path, _, log, save = completed_run()
    for sample in log.samples:
        sample.input = "PRIVATE_PROMPT_NOT_FOR_PUBLICATION"
        sample.metadata["private_path"] = "/private/PRIVATE_PATH_NOT_FOR_PUBLICATION"
        sample.store = {"actions": ["PRIVATE_ACTION_NOT_FOR_PUBLICATION"]}
    # Public order follows the core scenario order and then epoch, regardless
    # of the order in which the original evaluation completed its samples.
    log.samples.reverse()
    save()
    result = reporter.build_summary([path])["models"][0]
    assert [(sample["sample_id"], sample["epoch"]) for sample in result["samples"]] == [
        (scenario_id, epoch) for scenario_id in reporter.CORE_IDS for epoch in range(1, 4)
    ]
    allowed = {"sample_id", "epoch", "score", "completion", "violations", "shortfall_bps", "vs_twap_bps"}
    assert len(result["samples"]) == 18
    assert all(set(sample) == allowed for sample in result["samples"])
    assert result["samples"][0] == {
        "sample_id": reporter.CORE_IDS[0], "epoch": 1, "score": 0.11,
        "completion": 1.0, "violations": 0.0, "shortfall_bps": 1.0, "vs_twap_bps": 4.0,
    }
    assert "PRIVATE_" not in json.dumps(result)


def test_unregistered_task_name_is_also_accepted(completed_run):
    path, _, log, save = completed_run()
    log.eval.task = "execbench"
    save()
    assert reporter.build_summary([path])["models"][0]["n_samples"] == 18


def test_repeat_variability_is_distinct_from_dispersion_between_scenarios(completed_run):
    path, manifest, log, save = completed_run()
    # Every scenario averages 0.5, but the three suite repeats average 0.2,
    # 0.5 and 0.8. Their sample SD is 0.3, so repeat SEM is 0.3 / sqrt(3).
    epoch_scores = {1: 0.2, 2: 0.5, 3: 0.8}
    for sample in log.samples:
        score = sample.scores["execution_scorer"]
        score.value["score"] = epoch_scores[sample.epoch]
        explanation = json.loads(score.explanation)
        explanation["score"] = epoch_scores[sample.epoch]
        score.explanation = json.dumps(explanation)
    manifest["runs"][0]["mean_score"] = 0.5
    save()
    summary = reporter.build_summary([path])
    result = summary["models"][0]
    assert result["mean_score"] == 0.5
    assert result["score_sem"] == 0
    assert result["epoch_mean_scores"] == {"1": 0.2, "2": 0.5, "3": 0.8}
    assert result["repeat_score_sem"] == pytest.approx(0.3 / math.sqrt(3))
    table = reporter.markdown_table(summary)
    assert "| Score ± repeat SEM |" in table
    assert "| claude_cli/test-model | 0.500 ± 0.173 |" in table
    assert "0.500 ± 0.000" not in table
    assert "only three repeats" in table


def test_main_table_does_not_confuse_scenario_difficulty_with_repeat_variability(completed_run):
    path, _, _, _ = completed_run()
    summary = reporter.build_summary([path])
    result = summary["models"][0]
    # Suite epoch means are 0.36, 0.37, 0.38, whereas scenario means span
    # 0.12 through 0.62. The table should show repeat SEM 0.01 / sqrt(3).
    assert result["repeat_score_sem"] == pytest.approx(0.01 / math.sqrt(3))
    assert result["score_sem"] > 10 * result["repeat_score_sem"]
    assert "| claude_cli/test-model | 0.370 ± 0.006 |" in reporter.markdown_table(summary)


def test_requested_codex_identity_is_not_presented_as_independently_verified(completed_run):
    path, _, _, _ = completed_run("codex_cli/test-model")
    table = reporter.markdown_table(reporter.build_summary([path]))
    assert "Model labels are requested CLI identifiers" in table
    assert "Codex ID is not independently verified" in table
    assert "omits `observed_model`" in table


def test_different_task_cannot_be_reported_as_core_execbench(completed_run):
    path, _, log, save = completed_run()
    log.eval.task = "execbench/execbench_baseline"
    save()
    with pytest.raises(ValueError, match="different model or task"):
        reporter.build_summary([path])


def test_no_fill_placeholders_are_excluded_only_from_economic_means(completed_run):
    path, manifest, log, save = completed_run()
    sample_score = log.samples[0].scores["execution_scorer"]
    sample_score.value = {"score": 0.0, "completion": 0.0, "shortfall_bps": 0.0, "vs_twap_bps": 0.0, "violations": 0}
    sample_score.explanation = json.dumps({**sample_score.value, "shortfall_bps": None, "vs_twap_bps": None})
    manifest["runs"][0]["mean_score"] = statistics.mean(s.scores["execution_scorer"].value["score"] for s in log.samples)
    save()
    result = reporter.build_summary([path])["models"][0]
    assert result["n_no_fills"] == 1
    assert result["mean_completion"] == pytest.approx(17 / 18)
    assert result["mean_score"] == pytest.approx((18 * 0.37 - 0.11) / 18)
    assert result["mean_shortfall_bps"] == pytest.approx(sum(s.scores["execution_scorer"].value["shortfall_bps"] for s in log.samples[1:]) / 17)
    assert result["mean_vs_twap_bps"] == pytest.approx(50 / 17)


def test_all_no_fill_costs_remain_null_and_markdown_does_not_claim_zero_cost(completed_run):
    path, manifest, log, save = completed_run()
    for sample in log.samples:
        sample.scores["execution_scorer"].value = {
            "score": 0.0, "completion": 0.0, "shortfall_bps": 0.0, "vs_twap_bps": 0.0, "violations": 0,
        }
        sample.scores["execution_scorer"].explanation = json.dumps({
            **sample.scores["execution_scorer"].value, "shortfall_bps": None, "vs_twap_bps": None,
        })
    manifest["runs"][0]["mean_score"] = 0
    save()
    summary = reporter.build_summary([path])
    result = summary["models"][0]
    assert result["mean_shortfall_bps"] is result["mean_vs_twap_bps"] is None
    assert result["n_no_fills"] == 18
    assert result["mean_score"] == result["score_sem"] == 0
    assert "| — | — |" in reporter.markdown_table(summary)


@pytest.mark.parametrize("corruption", ["duplicate", "missing", "wrong_epoch", "wrong_scenario"])
def test_invalid_sample_sets_cannot_be_reported(completed_run, corruption):
    path, _, log, save = completed_run()
    if corruption == "duplicate":
        log.samples[-1] = log.samples[0].model_copy(deep=True)
    elif corruption == "missing":
        log.samples.pop()
    elif corruption == "wrong_epoch":
        log.samples[-1].epoch = 4
    else:
        log.samples[-1].id = "unrecognized"
    save()
    with pytest.raises(ValueError, match="18 scenario/epoch pairs"):
        reporter.build_summary([path])


@pytest.mark.parametrize("target", ["log", "sample"])
def test_error_never_becomes_a_reported_score(completed_run, target):
    path, _, log, save = completed_run()
    error = EvalError(message="Synthetic transport failure", traceback="", traceback_ansi="")
    if target == "log":
        log.status = "error"
        log.error = error
    else:
        log.samples[0].error = error
    save()
    with pytest.raises(ValueError, match="failed"):
        reporter.build_summary([path])


@pytest.mark.parametrize("limit_type", ["time", "token", "cost"])
def test_non_benchmark_limits_are_not_valid_scores(completed_run, limit_type):
    path, _, log, save = completed_run()
    log.samples[0].limit = EvalSampleLimit(type=limit_type, limit=160)
    save()
    with pytest.raises(ValueError, match="non-benchmark limit"):
        reporter.build_summary([path])


def test_message_limited_samples_are_counted_and_retained(completed_run):
    path, manifest, log, save = completed_run()
    sample = log.samples[0]
    sample.limit = EvalSampleLimit(type="message", limit=160)
    manifest["runs"][0]["limited_samples"] = [{
        "sample_id": sample.id, "epoch": sample.epoch, "limit": sample.limit.model_dump(mode="json"),
    }]
    save()
    result = reporter.build_summary([path])["models"][0]
    assert result["n_message_limited"] == 1
    assert result["n_samples"] == 18
    assert result["limited_samples"] == manifest["runs"][0]["limited_samples"]


@pytest.mark.parametrize("field,value", [
    ("status", "incomplete"), ("smoke_only", True), ("epochs", 1), ("git_commit", None),
])
def test_ineligible_manifests_are_rejected(completed_run, field, value):
    path, manifest, _, save = completed_run()
    manifest[field] = value
    save()
    with pytest.raises(ValueError):
        reporter.build_summary([path])


@pytest.mark.parametrize("field", ["git_commit", "source_sha256", "inspect_version", "sample_time_limit_seconds"])
def test_manifests_must_share_source_version_and_protocol(completed_run, field):
    first, _, _, _ = completed_run()
    second, manifest, _, save = completed_run("codex_cli/test-model", "second")
    manifest[field] = {"git_commit": "c" * 40, "source_sha256": "d" * 64,
                       "inspect_version": "0.3.999", "sample_time_limit_seconds": 1300}[field]
    save()
    with pytest.raises(ValueError, match="inconsistent.*provenance"):
        reporter.build_summary([first, second])


def test_duplicate_models_across_manifests_are_rejected(completed_run):
    first, _, _, _ = completed_run()
    second, _, _, _ = completed_run(directory="second")
    with pytest.raises(ValueError, match="multiple manifests"):
        reporter.build_summary([first, second])


@pytest.mark.parametrize("corruption", ["model", "source", "inspect", "scenario", "explanation"])
def test_log_must_match_its_manifest_and_core_parameters(completed_run, corruption):
    path, _, log, save = completed_run()
    if corruption == "model":
        log.eval.model = "claude_cli/different-model"
    elif corruption == "source":
        log.eval.metadata["source_sha256"] = "d" * 64
    elif corruption == "inspect":
        log.eval.packages["inspect_ai"] = "0.3.999"
    elif corruption == "scenario":
        log.samples[0].metadata["scenario"]["seed"] += 1
    else:
        log.samples[0].scores["execution_scorer"].explanation = "{}"
    save()
    with pytest.raises(ValueError):
        reporter.build_summary([path])


def test_outputs_are_byte_identical_and_manifest_order_does_not_matter(completed_run, tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    first, _, _, _ = completed_run()
    second, _, _, _ = completed_run("codex_cli/test-model", "second")
    summary = reporter.build_summary([first, second])
    assert reporter.build_summary([second, first]) == summary
    first_dir, second_dir = tmp_path / "output-a", tmp_path / "output-b"
    reporter.write_results(summary, first_dir)
    reporter.write_results(summary, second_dir)
    for filename in ("summary.json", "results.md", "scores_by_scenario.png"):
        assert (first_dir / filename).read_bytes() == (second_dir / filename).read_bytes()
    assert (first_dir / "scores_by_scenario.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    saved = json.loads((first_dir / "summary.json").read_text())
    assert saved["provenance"]["git_commit"] == "a" * 40
    assert list(saved["reference_scores"]) == ["dump", "twap"]
    assert len(saved["models"]) == 2
    # Files are real native Inspect JSON logs, read without any mocked reader.
    assert read_eval_log(first.parent / "synthetic.json").status == "success"
