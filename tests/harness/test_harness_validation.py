"""Contracts for harness validation surfaces."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_harness_check_tool_returns_structured_validation_result(git_repo: Path):
    runner = importlib.import_module("plugins.harness.runner")
    validation = importlib.import_module("plugins.harness.validation")
    started = runner.start_run("Validate from a model tool", repo_root=git_repo)

    payload = json.loads(validation.handle_harness_check({}))

    assert payload["success"] is True
    assert payload["transport"] == "completed"
    assert payload["validation_status"] in {"unverified", "blocked", "complete"}
    assert payload["validation_passed"] is False
    assert f"Harness check for {started['run_id']}" in payload["output"]
    assert '"ok": false' not in json.dumps(payload).lower()


def test_harness_check_tool_reports_check_errors_without_failed_tool_marker(monkeypatch: pytest.MonkeyPatch):
    validation = importlib.import_module("plugins.harness.validation")

    def raise_check(*args, **kwargs):
        raise ValueError("bad active task")

    monkeypatch.setattr(validation.runner, "run_check", raise_check)

    payload = json.loads(validation.handle_harness_check({"sensor_id": "generic.verification_freshness"}))

    assert payload["success"] is True
    assert payload["validation_status"] == "error"
    assert payload["validation_passed"] is False
    assert payload["sensor_id"] == "generic.verification_freshness"
    assert "Harness check error: bad active task" in payload["output"]
    assert '"success": false' not in json.dumps(payload).lower()


def test_harness_check_command_defaults_to_zero_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    harness_cli = importlib.import_module("plugins.harness.cli")

    def raise_check(*args, **kwargs):
        raise RuntimeError("cannot load task")

    monkeypatch.setattr(harness_cli.runner, "run_check", raise_check)

    assert harness_cli.main(["check"]) == 0
    assert "Harness check error: cannot load task" in capsys.readouterr().out


def test_harness_check_command_supports_strict_exit_code(monkeypatch: pytest.MonkeyPatch):
    harness_cli = importlib.import_module("plugins.harness.cli")
    monkeypatch.setattr(
        harness_cli.runner,
        "run_check",
        lambda sensor_id=None: "Harness check for run-test:\nFinalization: unverified",
    )

    assert harness_cli.main(["check", "--strict-exit-code"]) == 1


def test_harness_check_path_alias_passes_sensor_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    harness_cli = importlib.import_module("plugins.harness.cli")
    calls: list[str | None] = []

    def fake_check(sensor_id=None):
        calls.append(sensor_id)
        return "Harness check for run-test:\nFinalization: complete"

    monkeypatch.setattr(harness_cli.runner, "run_check", fake_check)

    assert harness_cli.check_main(["generic.git_status_clean"]) == 0
    assert calls == ["generic.git_status_clean"]
    assert "Finalization: complete" in capsys.readouterr().out
