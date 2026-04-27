"""Tests for the bundled harness plugin command registration."""

from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


class FakePluginContext:
    def __init__(self) -> None:
        self.commands: dict[str, dict[str, object]] = {}
        self.hooks: dict[str, object] = {}
        self.tools: dict[str, dict[str, object]] = {}

    def register_command(self, name: str, **kwargs: object) -> None:
        self.commands[name] = kwargs

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback

    def register_tool(self, name: str, **kwargs: object) -> None:
        self.tools[name] = kwargs


@pytest.fixture(autouse=True)
def _repo_root(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_registers_harness_slash_command_with_handler():
    plugin = importlib.import_module("plugins.harness")
    ctx = FakePluginContext()

    plugin.register(ctx)

    assert set(ctx.commands) == {"harness"}
    command = ctx.commands["harness"]
    harness_command = importlib.import_module("plugins.harness.command")
    assert command["handler"] is harness_command.handle_slash
    assert "deterministic harness runs" in command["description"]
    assert "status" in command["args_hint"]
    assert set(ctx.tools) == {
        "harness_check",
        "harness_report_dead_end",
        "harness_resolve_blockers",
    }


def test_registers_harness_hooks_with_handlers():
    plugin = importlib.import_module("plugins.harness")
    harness_hooks = importlib.import_module("plugins.harness.hooks")
    ctx = FakePluginContext()

    plugin.register(ctx)

    assert ctx.hooks == {
        "pre_tool_call": harness_hooks.pre_tool_call,
        "post_tool_call": harness_hooks.post_tool_call,
        "transform_tool_result": harness_hooks.transform_tool_result,
        "pre_llm_call": harness_hooks.pre_llm_call,
        "transform_final_response": harness_hooks.transform_final_response,
        "on_session_end": harness_hooks.on_session_end,
    }


def test_harness_status_reports_no_active_run():
    command = importlib.import_module("plugins.harness.command")

    assert command.handle_slash("status") == "no active harness run"


def test_harness_run_creates_active_run_and_event_log(_repo_root):
    command = importlib.import_module("plugins.harness.command")

    output = command.handle_slash("run ship the first harness slice")

    assert "Created harness run run-" in output
    harness_root = _repo_root / ".hermes-harness"
    assert (harness_root / ".gitignore").read_text(encoding="utf-8") == (
        "# Hermes harness runtime state\n"
        "/runs/\n"
        "/context/artifacts/\n"
    )

    active = harness_root / "active-run.json"
    assert active.exists()

    status = command.handle_slash("status")
    assert "Active harness run: run-" in status
    assert "Task: ship the first harness slice" in status

    event_logs = list((harness_root / "runs").glob("*/events.jsonl"))
    assert len(event_logs) == 1
    event_lines = [
        json.loads(line)
        for line in event_logs[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event_line["s"] for event_line in event_lines] == [1, 2]
    assert [event_line["t"] for event_line in event_lines] == ["run", "intent"]
    assert event_lines[0]["event"] == "created"
    assert event_lines[0]["run"].startswith("run-")
    assert event_lines[0]["ok"] is True
    assert event_lines[1]["latest_user_request"] == "ship the first harness slice"
    assert isinstance(event_lines[0]["ts"], str) and event_lines[0]["ts"].endswith("Z")
    assert "legacy free-text task skeleton (deprecated)" in output


def test_harness_run_delegates_to_runner(monkeypatch):
    command = importlib.import_module("plugins.harness.command")

    calls = []

    def fake_start_run(task):
        calls.append(task)
        return {
            "run_id": "run-delegated",
            "run_path": "/tmp/run-delegated",
            "task_path": "/tmp/run-delegated/task.yaml",
        }

    monkeypatch.setattr(command.runner, "start_run", fake_start_run)

    output = command.handle_slash("run ship delegated behavior")

    assert "Created harness run run-delegated." in output
    assert "Task file: /tmp/run-delegated/task.yaml" in output
    assert calls == ["ship delegated behavior"]


def test_harness_run_reports_openspec_source(_repo_root, monkeypatch):
    command = importlib.import_module("plugins.harness.command")
    change_dir = _repo_root / "openspec" / "changes" / "add-login"
    change_dir.mkdir(parents=True)
    fake_openspec = types.ModuleType("plugins.harness.openspec")
    fake_openspec.load_openspec_change = lambda path: {
        "id": "add-login",
        "summary": "Add login",
    }
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)

    output = command.handle_slash("run add-login")

    assert "Created harness run run-" in output
    assert "Source: OpenSpec" in output
    assert f"OpenSpec change: {change_dir.resolve()}" in output
    assert "Derived task snapshot:" in output
    run_dirs = list((_repo_root / ".hermes-harness" / "runs").glob("run-*"))
    assert run_dirs
    assert not (run_dirs[0] / "task.yaml").exists()


def test_harness_status_delegates_to_runner(monkeypatch):
    command = importlib.import_module("plugins.harness.command")

    monkeypatch.setattr(command.runner, "get_status", lambda: "runner-status")

    assert command.handle_slash("status") == "runner-status"


def test_harness_check_delegates_to_runner(monkeypatch):
    command = importlib.import_module("plugins.harness.command")

    monkeypatch.setattr(command.runner, "run_check", lambda sensor_id=None: f"runner-check {sensor_id}")

    assert command.handle_slash("check generic.claims_have_evidence") == "runner-check generic.claims_have_evidence"
