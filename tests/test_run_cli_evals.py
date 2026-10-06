"""Offline checks for evaluation completeness and reproducibility metadata."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from execbench.scenarios import SCENARIOS


_spec = spec_from_file_location(
    "run_cli_evals", Path(__file__).resolve().parents[1] / "scripts" / "run_cli_evals.py"
)
runner = module_from_spec(_spec)
_spec.loader.exec_module(runner)

CORE_IDS = [scenario.id for scenario in SCENARIOS]


@pytest.fixture
def complete_log():
    return SimpleNamespace(
        status="success",
        samples=[
            SimpleNamespace(
                id=scenario_id,
                epoch=epoch,
                error=None,
                scores={"execution_scorer": SimpleNamespace(value={"score": 0.75})},
                limit=None,
            )
            for scenario_id in CORE_IDS
            for epoch in range(1, 4)
        ],
    )


def test_complete_core_requires_all_eighteen_pairs(complete_log):
    assert len(complete_log.samples) == 18
    assert runner.validate_run(complete_log, CORE_IDS, epochs=3) == []


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "wrong_epoch", "wrong_scenario"])
def test_incomplete_or_mislabelled_core_is_not_accepted(complete_log, corruption):
    if corruption == "missing":
        complete_log.samples.pop()
    elif corruption == "duplicate":
        # Preserve the total count: checking only len(samples) is insufficient.
        complete_log.samples[-1] = complete_log.samples[0]
    elif corruption == "wrong_epoch":
        complete_log.samples[-1].epoch = 4
    else:
        complete_log.samples[-1].id = "unexpected_scenario"

    with pytest.raises(RuntimeError, match="scenario/epoch pairs"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


@pytest.mark.parametrize("status", ["error", "cancelled", "started"])
def test_failed_evaluation_cannot_become_a_scored_run(complete_log, status):
    complete_log.status = status
    with pytest.raises(RuntimeError, match="status"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


def test_sample_error_is_rejected_even_when_it_has_a_score(complete_log):
    complete_log.samples[0].error = SimpleNamespace(message="CLI transport failure")
    with pytest.raises(RuntimeError, match="failed or has no score"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


@pytest.mark.parametrize("scores", [None, {}, {"another_scorer": SimpleNamespace(value=1)}])
def test_missing_execution_score_is_rejected(complete_log, scores):
    complete_log.samples[0].scores = scores
    with pytest.raises(RuntimeError, match="failed or has no score"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


@pytest.mark.parametrize(
    "value",
    [
        None,
        0.75,
        {},
        {"score": "0.75"},
        {"score": True},
        {"score": float("nan")},
        {"score": float("inf")},
        {"score": float("-inf")},
        {"score": -0.01},
        {"score": 1.01},
    ],
)
def test_invalid_scores_are_rejected_before_aggregation(complete_log, value):
    complete_log.samples[0].scores["execution_scorer"].value = value
    with pytest.raises(RuntimeError, match="invalid score"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


@pytest.mark.parametrize("score", [0, 1, 0.0, 1.0])
def test_score_boundaries_are_valid(complete_log, score):
    complete_log.samples[0].scores["execution_scorer"].value = {"score": score}
    assert runner.validate_run(complete_log, CORE_IDS, epochs=3) == []


def test_message_limit_is_retained_with_the_sample_score(complete_log):
    class SampleLimit:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {"type": "message", "limit": 160}

    sample = complete_log.samples[4]
    sample.limit = SampleLimit()
    original_scores = sample.scores

    assert runner.validate_run(complete_log, CORE_IDS, epochs=3) == [
        {"sample_id": sample.id, "epoch": sample.epoch, "limit": {"type": "message", "limit": 160}}
    ]
    assert sample.scores is original_scores
    assert sample.scores["execution_scorer"].value["score"] == 0.75


@pytest.mark.parametrize("limit_type", ["time", "working", "token", "cost", "unknown"])
def test_other_limits_cannot_be_published_as_completed_runs(complete_log, limit_type):
    class SampleLimit:
        def model_dump(self, *, mode):
            return {"type": limit_type, "limit": 1200}

    complete_log.samples[0].limit = SampleLimit()
    with pytest.raises(RuntimeError, match="non-message limit"):
        runner.validate_run(complete_log, CORE_IDS, epochs=3)


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is required for source provenance checks")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    (tmp_path / "execbench").mkdir()
    (tmp_path / "execbench" / "sim.py").write_text("VALUE = 1\n")
    (tmp_path / ".gitignore").write_text("ignored.py\n")

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=ExecBench Test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=tmp_path, text=True, capture_output=True, check=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "Synthetic test sources")
    return tmp_path, git


def test_full_run_records_committed_sources_without_rejecting_generated_logs(source_repo):
    directory, git = source_repo
    expected = git("rev-parse", "HEAD")
    assert runner.source_commit(require_clean=True) == expected
    (directory / "logs").mkdir()
    (directory / "logs" / "result.json").write_text('{}\n')
    assert runner.source_commit(require_clean=True) == expected


@pytest.mark.parametrize("change", ["modified", "staged", "deleted", "untracked", "ignored"])
def test_full_run_requires_clean_tracked_sources(source_repo, change):
    directory, git = source_repo
    source = directory / "execbench" / "sim.py"
    if change in ("modified", "staged"):
        source.write_text("VALUE = 2\n")
        if change == "staged":
            git("add", "execbench/sim.py")
    elif change == "deleted":
        source.unlink()
    else:
        (source.parent / f"{change}.py").write_text("VALUE = 3\n")
    with pytest.raises(RuntimeError, match="tracked and unchanged"):
        runner.source_commit(require_clean=True)
    assert runner.source_commit(require_clean=False) == git("rev-parse", "HEAD")


def test_only_smoke_runs_may_proceed_without_a_git_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner.source_commit(require_clean=False) is None
    with pytest.raises(RuntimeError, match="Git|Commit"):
        runner.source_commit(require_clean=True)


def test_fingerprint_changes_with_source_but_not_generated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "execbench"\n')
    source_dir = tmp_path / "execbench"
    source_dir.mkdir()
    source = source_dir / "scoring.py"
    source.write_text("SCORE_WEIGHT = 1\n")
    original = runner.source_fingerprint()

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "run-manifest.json").write_text('{"status": "running"}\n')
    assert runner.source_fingerprint() == original

    source.write_text("SCORE_WEIGHT = 2\n")
    changed = runner.source_fingerprint()
    assert changed != original

    source.rename(source_dir / "renamed_scoring.py")
    assert runner.source_fingerprint() != changed


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--call-timeout", "nan"),
        ("--call-timeout", "inf"),
        ("--call-timeout", "-1"),
        ("--call-timeout", "0"),
        ("--sample-time-limit", "-1"),
        ("--sample-time-limit", "0"),
    ],
)
def test_invalid_timeout_stops_before_loading_or_calling_models(monkeypatch, option, value):
    monkeypatch.setattr(runner.sys, "argv", ["run_cli_evals.py", option, value])

    def unexpected_run(_args):
        pytest.fail("Invalid arguments must be rejected before starting a run")

    monkeypatch.setattr(runner, "run", unexpected_run)
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2
