"""Contract tests for harness runner orchestration."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from plugins.harness import events


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner():
    return importlib.import_module("plugins.harness.runner")


def _event_rows(repo: Path) -> list[dict[str, object]]:
    event_logs = list((repo / ".hermes-harness" / "runs").glob("*/events.jsonl"))
    assert len(event_logs) == 1
    return [
        json.loads(line)
        for line in event_logs[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _commit_harness_ignore(repo: Path) -> None:
    (repo / ".gitignore").write_text(".hermes-harness/\nhermes_test/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Hermes Test",
            "-c",
            "user.email=hermes@example.invalid",
            "commit",
            "-m",
            "ignore harness state",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_start_run_writes_task_active_marker_and_initial_events(git_repo, runner):
    result = runner.start_run("Ship the first harness slice", repo_root=git_repo)

    harness_root = git_repo / ".hermes-harness"
    active = json.loads((harness_root / "active-run.json").read_text(encoding="utf-8"))
    run_id = active["run_id"]
    run_dir = harness_root / "runs" / run_id

    assert run_id.startswith("run-")
    assert result["run_id"] == run_id
    assert run_dir.is_dir()
    assert (run_dir / "task.yaml").is_file()
    assert "Ship the first harness slice" in (run_dir / "task.yaml").read_text(encoding="utf-8")

    rows = _event_rows(git_repo)
    assert [row["s"] for row in rows] == [1, 2]
    assert [row["t"] for row in rows] == ["run", "intent"]
    assert rows[0]["run"] == run_id
    assert rows[0]["ok"] is True
    assert rows[1]["latest_user_request"] == "Ship the first harness slice"
    assert rows[1]["ok"] is True


def test_start_run_accepts_openspec_change_id_without_authoritative_task_yaml(
    git_repo,
    runner,
    monkeypatch,
):
    change_dir = git_repo / "openspec" / "changes" / "add-login"
    change_dir.mkdir(parents=True)
    fake_openspec = types.ModuleType("plugins.harness.openspec")
    calls = []

    def load_openspec_change(path):
        calls.append(path)
        return {
            "id": "add-login",
            "objective": {"summary": "Add OpenSpec login"},
            "intent": {"latest_user_request": "Add OpenSpec login"},
            "acceptance_matrix": [
                {
                    "id": "login-works",
                    "criterion": "Login works",
                    "required_evidence": [],
                }
            ],
            "version": "1",
            "finalization_policy": {"complete_requires": ["login-works"]},
        }

    fake_openspec.load_openspec_change = load_openspec_change
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)

    result = runner.start_run("add-login", repo_root=git_repo)

    run_dir = Path(result["run_path"])
    active = json.loads((git_repo / ".hermes-harness" / "active-run.json").read_text(encoding="utf-8"))
    metadata = active["metadata"]
    assert calls == [change_dir.resolve()]
    assert result["source_format"] == "openspec"
    assert metadata["source_format"] == "openspec"
    assert metadata["source_path"] == str(change_dir.resolve())
    assert not (run_dir / "task.yaml").exists()
    snapshot_path = run_dir / "task.snapshot.json"
    assert snapshot_path.is_file()
    assert result["task_snapshot_path"] == str(snapshot_path)

    status = runner.get_status(repo_root=git_repo)
    assert "Source: OpenSpec" in status
    assert "Derived task snapshot:" in status
    assert "Add OpenSpec login" in status


def test_start_run_accepts_existing_openspec_change_path(git_repo, runner, monkeypatch):
    change_dir = git_repo / "custom-change"
    change_dir.mkdir()
    fake_openspec = types.ModuleType("plugins.harness.openspec")
    fake_openspec.load_openspec_change = lambda path: {
        "id": "custom-change",
        "summary": "Custom OpenSpec path",
    }
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)

    result = runner.start_run(str(change_dir), repo_root=git_repo)

    assert result["source_format"] == "openspec"
    assert result["source_path"] == str(change_dir.resolve())
    assert not (Path(result["run_path"]) / "task.yaml").exists()


def test_get_status_without_active_run_is_clear_and_does_not_create_harness_dir(git_repo, runner):
    status = runner.get_status(repo_root=git_repo)

    assert "no active harness run" in status.lower()
    assert not (git_repo / ".hermes-harness").exists()


def test_get_status_with_active_run_includes_run_id_and_task_summary(git_repo, runner):
    started = runner.start_run("Summarize active harness run", repo_root=git_repo)

    status = runner.get_status(repo_root=git_repo)

    assert started["run_id"] in status
    assert "Summarize active harness run" in status
    assert "legacy free-text task skeleton (deprecated)" in status


def test_run_check_without_active_run_is_clear(git_repo, runner):
    output = runner.run_check(repo_root=git_repo)

    assert "no active harness run" in output.lower()


def test_run_check_with_active_run_reports_ledger_sensor_statuses(git_repo, runner):
    runner.start_run("Check ledger sensor statuses", repo_root=git_repo)

    output = runner.run_check(repo_root=git_repo)

    assert "generic.claims_have_evidence" in output
    assert "generic.verification_freshness" in output
    assert "generic.no_unresolved_failed_tools" in output
    assert "ok=" in output.lower() or "pass" in output.lower() or "fail" in output.lower()


def test_run_check_git_status_uses_requested_repo_root_when_cwd_differs(git_repo, runner, tmp_path, monkeypatch):
    _commit_harness_ignore(git_repo)
    runner.start_run("Check requested repo root", repo_root=git_repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    output = runner.run_check(repo_root=git_repo, sensor_id="generic.git_status_clean")

    assert "- generic.git_status_clean: ok" in output
    assert "working tree clean" in output


def test_run_check_git_status_does_not_become_unknown_ledger_sensor(git_repo, runner):
    _commit_harness_ignore(git_repo)
    runner.start_run("Check git status without ledger confusion", repo_root=git_repo)

    output = runner.run_check(repo_root=git_repo, sensor_id="generic.git_status_clean")
    rows = _event_rows(git_repo)
    sensor_rows = [row for row in rows if row["t"] == "sensor"]

    assert "unknown ledger sensor id: generic.git_status_clean" not in output
    assert "- generic.git_status_clean: ok" in output
    assert sensor_rows[-1]["id"] == "generic.git_status_clean"
    assert sensor_rows[-1]["ok"] is True


def test_run_check_can_run_command_sensor_from_controls_file(git_repo, runner):
    runner.start_run("Run command sensor from controls", repo_root=git_repo)
    controls_path = git_repo / ".hermes-harness" / "controls.yaml"
    controls_path.write_text(
        "\n".join(
            [
                "version: '1'",
                "sensors:",
                "  - id: tests.command.from_controls",
                "    kind: command",
                "    command: test -f marker.txt",
                "    covers: manual-verification",
                "  - id: generic.verification_freshness",
                "    kind: ledger",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (git_repo / "marker.txt").write_text("present\n", encoding="utf-8")

    output = runner.run_check(repo_root=git_repo, sensor_id="tests.command.from_controls")

    assert "- tests.command.from_controls: ok" in output
    assert "exited with code 0" in output


def test_run_check_uses_openspec_snapshot(git_repo, runner, monkeypatch):
    change_dir = git_repo / "openspec" / "changes" / "check-open"
    change_dir.mkdir(parents=True)
    fake_openspec = types.ModuleType("plugins.harness.openspec")
    fake_openspec.load_openspec_change = lambda path: {
        "version": "1",
        "id": "check-open",
        "objective": {"summary": "Check OpenSpec snapshot"},
        "intent": {"latest_user_request": "Check OpenSpec snapshot"},
        "acceptance_matrix": [
            {
                "id": "manual-verification",
                "criterion": "Verified",
                "required_evidence": [],
            }
        ],
    }
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)
    runner.start_run("check-open", repo_root=git_repo)

    output = runner.run_check(repo_root=git_repo)

    assert "Harness check for run-" in output
    assert "generic.claims_have_evidence" in output


def test_run_openspec_validate_reports_missing_cli(git_repo, runner, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)

    result = runner.run_openspec_validate("add-login", repo_root=git_repo)

    assert result.id == "openspec.validate.strict"
    assert result.ok is False
    assert "not available" in result.message


def test_run_openspec_validate_runs_strict_cli(git_repo, runner, monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = "valid\n"
        stderr = ""

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/openspec")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or Completed(),
    )

    result = runner.run_openspec_validate("add-login", repo_root=git_repo)

    assert result.ok is True
    assert calls[0][0] == ["openspec", "validate", "add-login", "--strict"]
    assert calls[0][1]["cwd"] == git_repo


def test_status_reflects_fresh_acceptance_then_later_mutation_stales_it(git_repo, runner):
    started = runner.start_run("Track finalization freshness", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    log.append("sensor", {"id": "tests.focused", "covers": ["manual-verification"], "ok": True})

    covered_status = runner.get_status(repo_root=git_repo)

    assert "Finalization: complete" in covered_status

    log.append("tool", {"name": "edit", "fx": "mutate", "ok": True})

    stale_status = runner.get_status(repo_root=git_repo)

    assert "Finalization: unverified" in stale_status
    assert "manual-verification" in stale_status


def test_create_task_skeleton_contains_summary_and_acceptance_matrix(runner):
    task = runner.create_task_skeleton("run-test", "Create a useful harness task")

    assert task["id"] == "run-test"
    assert task["objective"]["summary"] == "Create a useful harness task"
    assert task["intent"]["latest_user_request"] == "Create a useful harness task"
    assert task["acceptance_matrix"]
    assert isinstance(task["acceptance_matrix"][0]["required_evidence"], list)
