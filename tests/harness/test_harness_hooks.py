"""Contract tests for harness plugin hooks."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from plugins.harness import events, paths, runner
from plugins.harness.finalization import evaluate
from plugins.harness.sensors import run_ledger_sensor
from plugins.harness.task import load_task


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def hooks():
    return importlib.import_module("plugins.harness.hooks")


class FakePluginContext:
    def __init__(self) -> None:
        self.hooks: dict[str, object] = {}

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback


def _event_rows(repo: Path) -> list[dict[str, object]]:
    run_id = paths.get_active_run(repo)
    assert run_id is not None
    return [
        event.to_dict()
        for event in events.EventLog.for_run(run_id, repo).read_all()
    ]


def _write_read_only_task(repo: Path, run_id: str) -> None:
    task_path = paths.run_dir(run_id, repo, create=False) / runner.TASK_FILENAME
    task_path.write_text(
        json.dumps(
            {
                "version": "1",
                "id": run_id,
                "objective": {"summary": "Read only task"},
                "intent": {
                    "latest_user_request": "Inspect the repo without writing",
                    "read_only": True,
                },
                "acceptance_matrix": [
                    {
                        "id": "manual",
                        "criterion": "Manual verification",
                        "required_evidence": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _load_active_task(repo: Path):
    run_id = paths.get_active_run(repo)
    assert run_id is not None
    return load_task(paths.run_dir(run_id, repo, create=False) / runner.TASK_FILENAME)


def _install_fake_openspec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    acceptance_id: str = "openspec-ready",
    read_only: bool = False,
) -> None:
    fake_openspec = types.ModuleType("plugins.harness.openspec")

    def load_openspec_change(path: Path):
        return {
            "version": "1",
            "id": path.name,
            "objective": {"summary": "OpenSpec hook task"},
            "intent": {
                "latest_user_request": "OpenSpec hook task",
                "read_only": read_only,
            },
            "constraints": {"filesystem": {"read_only": read_only}},
            "acceptance_matrix": [
                {
                    "id": acceptance_id,
                    "criterion": "OpenSpec criteria are satisfied",
                    "required_evidence": [],
                }
            ],
            "finalization_policy": {"complete_requires": [acceptance_id]},
        }

    fake_openspec.load_openspec_change = load_openspec_change
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)


def test_register_hooks_includes_required_hook_names(hooks):
    ctx = FakePluginContext()

    hooks.register_hooks(ctx)

    assert set(ctx.hooks) == {
        "pre_tool_call",
        "post_tool_call",
        "transform_tool_result",
        "pre_llm_call",
        "transform_final_response",
        "on_session_end",
    }
    assert ctx.hooks["pre_tool_call"] is hooks.pre_tool_call
    assert ctx.hooks["post_tool_call"] is hooks.post_tool_call
    assert ctx.hooks["transform_tool_result"] is hooks.transform_tool_result
    assert ctx.hooks["pre_llm_call"] is hooks.pre_llm_call
    assert ctx.hooks["transform_final_response"] is hooks.transform_final_response
    assert ctx.hooks["on_session_end"] is hooks.on_session_end


def test_hooks_noop_when_no_active_run(git_repo: Path, hooks):
    assert hooks.pre_llm_call() is None
    assert hooks.pre_tool_call(tool_name="terminal", args={"command": "echo ok"}) is None
    assert hooks.post_tool_call(tool_name="terminal", args={"command": "echo ok"}, result="ok") is None
    assert (
        hooks.transform_tool_result(
            tool_name="terminal",
            args={"command": "echo ok"},
            result="ok",
        )
        is None
    )
    assert hooks.on_session_end(session_id="session-1", completed=True, interrupted=False) is None
    assert (
        hooks.transform_final_response(
            session_id="session-1",
            task_id="task-1",
            final_response="Done.",
            completed=True,
            interrupted=False,
            conversation_history=[],
            model="test-model",
            platform="cli",
        )
        is None
    )
    assert not (git_repo / ".hermes-harness").exists()


def test_pre_llm_call_injects_active_harness_context(git_repo: Path, hooks):
    runner.start_run("Keep working until the harness is complete", repo_root=git_repo)

    injected = hooks.pre_llm_call(
        session_id="session-1",
        user_message="only ever stop when that harness is complete",
        conversation_history=[],
        model="test-model",
        platform="cli",
    )

    assert isinstance(injected, dict)
    context = injected["context"]
    assert "<active_harness_run>" in context
    assert "Keep working until the harness is complete" in context
    assert "manual-verification" in context
    assert "Do not switch to unrelated skills or tasks" in context
    assert "harness_check" in context
    assert "harness_report_dead_end" in context


def test_post_tool_call_appends_tool_finished_event_to_active_run(git_repo: Path, hooks):
    started = runner.start_run("Record completed tool calls", repo_root=git_repo)

    assert hooks.post_tool_call(
        tool_name="read_file",
        args={"path": "README.md"},
        result="contents",
        task_id="task-1",
        duration_ms=12,
    ) is None

    rows = _event_rows(git_repo)
    assert rows[-1]["t"] == "tool"
    assert rows[-1]["event"] == "finished"
    assert rows[-1]["run"] == started["run_id"]
    assert rows[-1]["tool"] == "read_file"
    assert rows[-1]["ok"] is True
    assert rows[-1]["duration_ms"] == 12


def test_transform_tool_result_tombstones_large_result_and_appends_event(git_repo: Path, hooks):
    runner.start_run("Compact large tool output", repo_root=git_repo)
    result = "large-output\n" * 2000

    transformed = hooks.transform_tool_result(
        tool_name="terminal",
        args={"command": "cat huge.log"},
        result=result,
    )

    tombstone = json.loads(transformed)
    assert tombstone["harness_tombstone"] is True
    assert tombstone["artifact_path"].startswith(".hermes-harness/context/artifacts/")
    assert (git_repo / tombstone["artifact_path"]).read_text(encoding="utf-8") == result

    rows = _event_rows(git_repo)
    assert rows[-1]["t"] == "context"
    assert rows[-1]["event"] == "tool_result_tombstoned"
    assert rows[-1]["tool"] == "terminal"
    assert rows[-1]["artifact_path"] == tombstone["artifact_path"]
    assert rows[-1]["original_chars"] == len(result)


def test_pre_tool_call_blocks_write_like_tool_for_read_only_task_and_records_policy_event(
    git_repo: Path,
    hooks,
):
    started = runner.start_run("Read-only policy", repo_root=git_repo)
    _write_read_only_task(git_repo, started["run_id"])

    decision = hooks.pre_tool_call(
        tool_name="write_file",
        args={"path": "generated.txt", "content": "mutation"},
        task_id="task-1",
    )

    assert decision == {
        "action": "block",
        "message": "harness read-only policy blocked mutating tool: write_file",
    }
    rows = _event_rows(git_repo)
    assert rows[-1]["t"] == "policy"
    assert rows[-1]["event"] == "blocked"
    assert rows[-1]["policy"] == "read_only"
    assert rows[-1]["action"] == "violation"
    assert rows[-1]["tool"] == "write_file"
    assert rows[-1]["reason"] == "read_only"
    assert rows[-1]["ok"] is False


def test_pre_tool_call_loads_read_only_policy_from_openspec_snapshot(
    git_repo: Path,
    hooks,
    monkeypatch: pytest.MonkeyPatch,
):
    change_dir = git_repo / "openspec" / "changes" / "readonly-change"
    change_dir.mkdir(parents=True)
    _install_fake_openspec(monkeypatch, read_only=True)
    started = runner.start_run("readonly-change", repo_root=git_repo)

    assert not (Path(started["run_path"]) / runner.TASK_FILENAME).exists()

    decision = hooks.pre_tool_call(
        tool_name="write_file",
        args={"path": "generated.txt", "content": "mutation"},
        task_id="task-1",
    )

    assert decision == {
        "action": "block",
        "message": "harness read-only policy blocked mutating tool: write_file",
    }
    rows = _event_rows(git_repo)
    assert rows[-1]["t"] == "policy"
    assert rows[-1]["policy"] == "read_only"
    assert rows[-1]["tool"] == "write_file"
    assert rows[-1]["ok"] is False


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("scripts/run_tests.sh tests/harness/test_harness_hooks.py", "verify"),
        ("rm -rf build && sed -i 's/a/b/' README.md", "mutate"),
    ],
)
def test_terminal_classification_distinguishes_verify_from_mutate(hooks, command: str, expected: str):
    assert hooks.classify_tool("terminal", {"command": command}) == expected


def test_mixed_verify_then_mutate_terminal_command_is_blocked_for_read_only_task(
    git_repo: Path,
    hooks,
):
    started = runner.start_run("Read-only mixed terminal command", repo_root=git_repo)
    _write_read_only_task(git_repo, started["run_id"])

    decision = hooks.pre_tool_call(
        tool_name="terminal",
        args={"command": "scripts/run_tests.sh tests/foo && rm -rf build"},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
    )

    assert hooks.classify_tool(
        "terminal",
        {"command": "scripts/run_tests.sh tests/foo && rm -rf build"},
    ) == "mutate"
    assert decision == {
        "action": "block",
        "message": "harness read-only policy blocked mutating tool: terminal",
    }

    rows = _event_rows(git_repo)
    policy = rows[-1]
    assert policy["t"] == "policy"
    assert policy["policy"] == "read_only"
    assert policy["action"] == "violation"
    assert policy["tool"] == "terminal"
    assert policy["fx"] == "mutate"
    assert policy["args"] == {"command": "scripts/run_tests.sh tests/foo && rm -rf build"}
    assert policy["ok"] is False


def test_blocked_read_only_tool_event_blocks_sensor_and_finalization(git_repo: Path, hooks):
    started = runner.start_run("Read-only finalization blocking", repo_root=git_repo)
    _write_read_only_task(git_repo, started["run_id"])

    hooks.pre_tool_call(
        tool_name="write_file",
        args={"path": "generated.txt", "content": "mutation"},
        task_id="task-1",
    )
    rows = events.EventLog.for_run(started["run_id"], git_repo).read_all()
    task = _load_active_task(git_repo)

    sensor = run_ledger_sensor("generic.no_read_only_violation", task, rows)
    final = evaluate(task, rows, sensor_ids=["generic.no_read_only_violation"])

    assert sensor.ok is False
    assert sensor.blocking == (str(rows[-1].s),)
    assert final.status == "blocked"
    assert str(rows[-1].s) in final.blocking


def test_transform_final_response_noops_when_interrupted(git_repo: Path, hooks):
    runner.start_run("Interrupted final response", repo_root=git_repo)

    transformed = hooks.transform_final_response(
        session_id="session-1",
        task_id="task-1",
        final_response="Partial answer.",
        completed=False,
        interrupted=True,
        conversation_history=[],
        model="test-model",
        platform="cli",
    )

    assert transformed is None


def test_transform_final_response_reports_blocked_active_run(git_repo: Path, hooks):
    started = runner.start_run("Report blocked final response", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    failed = log.append("tool", {"event": "finished", "tool": "terminal", "fx": "verify", "ok": False})

    transformed = hooks.transform_final_response(
        session_id="session-1",
        task_id="task-1",
        final_response="Done.",
        completed=True,
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="cli",
    )

    assert isinstance(transformed, dict)
    report = transformed["final_response"]
    assert transformed["metadata"]["harness"]["status"] == "blocked"
    assert "Status: blocked" in report
    assert "Missing acceptance IDs: manual-verification" in report
    assert f"Blocking IDs: {failed.s}" in report
    assert "Original response\nDone." in report

    rows = _event_rows(git_repo)
    assert rows[-1]["t"] == "finalization"
    assert rows[-1]["event"] == "transform_final_response"
    assert rows[-1]["status"] == "blocked"
    assert rows[-1]["ok"] is False


def test_transform_final_response_reports_complete_active_run(git_repo: Path, hooks):
    started = runner.start_run("Report complete final response", repo_root=git_repo)
    events.EventLog.for_run(started["run_id"], git_repo).append(
        "sensor",
        {"id": "tests.focused", "covers": ["manual-verification"], "ok": True},
    )

    transformed = hooks.transform_final_response(
        session_id="session-1",
        task_id="task-1",
        final_response="Completed with focused tests.",
        completed=True,
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="cli",
    )

    assert isinstance(transformed, dict)
    report = transformed["final_response"]
    assert transformed["metadata"]["harness"]["status"] == "complete"
    assert "Status: complete" in report
    assert "Complete acceptance IDs: manual-verification" in report
    assert "Missing acceptance IDs: none" in report
    assert "no extra completion is claimed beyond the listed IDs" in report


def test_transform_final_response_loads_metadata_and_report_from_openspec_snapshot(
    git_repo: Path,
    hooks,
    monkeypatch: pytest.MonkeyPatch,
):
    change_dir = git_repo / "openspec" / "changes" / "final-open"
    change_dir.mkdir(parents=True)
    _install_fake_openspec(monkeypatch, acceptance_id="openspec-ready")
    started = runner.start_run("final-open", repo_root=git_repo)
    events.EventLog.for_run(started["run_id"], git_repo).append(
        "sensor",
        {"id": "tests.openspec", "covers": ["openspec-ready"], "ok": True},
    )

    transformed = hooks.transform_final_response(
        session_id="session-1",
        task_id="task-1",
        final_response="Completed OpenSpec task.",
        completed=True,
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="cli",
    )

    assert not (Path(started["run_path"]) / runner.TASK_FILENAME).exists()
    assert isinstance(transformed, dict)
    report = transformed["final_response"]
    assert transformed["metadata"]["harness"]["run_id"] == started["run_id"]
    assert transformed["metadata"]["harness"]["status"] == "complete"
    assert transformed["metadata"]["harness"]["complete_ids"] == ["openspec-ready"]
    assert "Status: complete" in report
    assert "Complete acceptance IDs: openspec-ready" in report
    assert "Original response\nCompleted OpenSpec task." in report
