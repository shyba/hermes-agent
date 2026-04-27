"""Contract tests for harness command and git sensors."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from plugins.harness.controls import SensorSpec
from plugins.harness.sensors import run_command_sensor, run_git_sensor, run_sensor


def test_command_sensor_reports_success_from_tmp_path(tmp_path):
    sensor = SensorSpec(
        id="tests.command.success",
        command="printf 'sensor ok'",
        covers=("ac-command",),
    )

    result = run_command_sensor(sensor, repo_root=str(tmp_path))

    assert result.id == "tests.command.success"
    assert result.ok is True
    assert result.covers == ("ac-command",)
    assert "exited with code 0" in result.message
    assert "sensor ok" in result.message


def test_command_sensor_reports_failure_from_tmp_path(tmp_path):
    sensor = {
        "id": "tests.command.failure",
        "command": "sh -c 'printf sensor-failed; exit 7'",
        "covers": ["ac-command"],
    }

    result = run_sensor(sensor, repo_root=str(tmp_path))

    assert result.id == "tests.command.failure"
    assert result.ok is False
    assert result.covers == ("ac-command",)
    assert "exited with code 7" in result.message
    assert "sensor-failed" in result.message


def test_git_status_sensor_reports_clean_and_dirty_tmp_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / ".gitignore").write_text("hermes_test/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Hermes Test",
            "-c",
            "user.email=hermes@example.invalid",
            "commit",
            "-m",
            "ignore hermes test home",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    clean = run_git_sensor("generic.git_status_clean", repo_root=str(tmp_path))

    assert clean.id == "generic.git_status_clean"
    assert clean.ok is True
    assert "working tree clean" in clean.message

    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = run_sensor("generic.git_status_clean", repo_root=str(tmp_path))

    assert dirty.id == "generic.git_status_clean"
    assert dirty.ok is False
    assert "dirty.txt" in dirty.message
