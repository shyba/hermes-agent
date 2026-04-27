"""Integration contracts for final-response enforcement hooks."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from hermes_cli import plugins
from plugins.harness import events


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def harness_runner():
    return importlib.import_module("plugins.harness.runner")


@pytest.fixture
def harness_hooks():
    return importlib.import_module("plugins.harness.hooks")


def _require_harness_final_transform(harness_hooks: Any):
    transform = getattr(harness_hooks, "transform_final_response", None)
    if transform is None:
        pytest.xfail("plugins.harness.hooks.transform_final_response is not landed yet")
    return transform


def _transform_status(result: Any) -> str:
    if isinstance(result, dict):
        harness = result.get("harness")
        if isinstance(harness, dict) and isinstance(harness.get("status"), str):
            return harness["status"]
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            metadata_harness = metadata.get("harness")
            if (
                isinstance(metadata_harness, dict)
                and isinstance(metadata_harness.get("status"), str)
            ):
                return metadata_harness["status"]
            if isinstance(metadata.get("harness_status"), str):
                return metadata["harness_status"]
        if isinstance(result.get("status"), str):
            return result["status"]
        final_response = result.get("final_response")
    else:
        final_response = result

    assert isinstance(final_response, str)
    lowered = final_response.lower()
    for status in ("blocked", "complete", "partial", "unverified"):
        if status in lowered:
            return status
    raise AssertionError(f"could not infer harness status from {final_response!r}")


def _call_harness_transform(transform: Any, final_response: str) -> Any:
    return transform(
        session_id="session-1",
        task_id="task-1",
        final_response=final_response,
        completed=True,
        partial=False,
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="pytest",
    )


def _replacement_text(result: Any) -> str:
    if isinstance(result, dict):
        value = result.get("final_response")
    else:
        value = result
    assert isinstance(value, str)
    return value


def _install_fake_openspec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    acceptance_id: str = "openspec-final",
) -> None:
    fake_openspec = types.ModuleType("plugins.harness.openspec")

    def load_openspec_change(path: Path):
        return {
            "version": "1",
            "id": path.name,
            "objective": {"summary": "OpenSpec final response task"},
            "intent": {"latest_user_request": "OpenSpec final response task"},
            "acceptance_matrix": [
                {
                    "id": acceptance_id,
                    "criterion": "OpenSpec final response criteria are satisfied",
                    "required_evidence": [],
                }
            ],
            "finalization_policy": {"complete_requires": [acceptance_id]},
        }

    fake_openspec.load_openspec_change = load_openspec_change
    monkeypatch.setitem(sys.modules, "plugins.harness.openspec", fake_openspec)


def test_generic_final_response_helper_applies_transform_without_harness(monkeypatch: pytest.MonkeyPatch):
    def fake_invoke_hook(hook_name: str, **kwargs: Any) -> list[Any]:
        assert hook_name == "transform_final_response"
        assert kwargs["final_response"] == "original"
        return ["transformed"]

    monkeypatch.setattr(plugins, "invoke_hook", fake_invoke_hook)

    result = plugins.apply_final_response_transforms(
        final_response="original",
        completed=True,
        partial=False,
        session_id="session-1",
    )

    assert result == {
        "final_response": "transformed",
        "completed": True,
        "partial": False,
    }


def test_harness_final_transform_noops_without_active_run(harness_hooks):
    transform = _require_harness_final_transform(harness_hooks)

    result = _call_harness_transform(transform, "ordinary final prose")

    assert result is None or result == {
        "final_response": "ordinary final prose",
        "completed": True,
        "partial": False,
    }


def test_harness_final_transform_returns_unverified_report_without_evidence(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    transform = _require_harness_final_transform(harness_hooks)
    harness_runner.start_run("Enforce final response evidence", repo_root=git_repo)

    result = _call_harness_transform(transform, "Done.")

    assert _transform_status(result) == "unverified"
    assert "unverified" in _replacement_text(result).lower()
    assert result["completed"] is False
    assert result["partial"] is True
    assert "pending_steer" in result
    assert "Continue working now" in result["pending_steer"]


def test_harness_final_transform_returns_blocked_when_read_only_policy_blocker_exists(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    transform = _require_harness_final_transform(harness_hooks)
    started = harness_runner.start_run("Report read-only blocker", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    log.append(
        "policy",
        {
            "event": "blocked",
            "policy": "read_only",
            "action": "violation",
            "tool": "write_file",
            "ok": False,
        },
    )

    result = _call_harness_transform(transform, "All done.")

    assert _transform_status(result) == "blocked"


def test_harness_final_transform_returns_complete_when_required_evidence_exists(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    transform = _require_harness_final_transform(harness_hooks)
    started = harness_runner.start_run("Report complete with evidence", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    log.append(
        "sensor",
        {
            "id": "tests.focused",
            "covers": ["manual-verification"],
            "ok": True,
        },
    )

    result = _call_harness_transform(transform, "All done.")

    assert _transform_status(result) == "complete"
    assert "pending_steer" not in result


def test_harness_dead_end_tool_stops_auto_continuation(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    from plugins.harness.continuation import handle_report_dead_end

    transform = _require_harness_final_transform(harness_hooks)
    harness_runner.start_run("Stop on real dead end", repo_root=git_repo)

    recorded = handle_report_dead_end(
        {
            "reason": "The required service is not available in this environment.",
            "blocker_ids": ["manual-verification"],
            "attempts": ["Checked local configuration"],
        }
    )
    assert json.loads(recorded)["success"] is True

    result = _call_harness_transform(transform, "Done.")

    assert _transform_status(result) == "unverified"
    assert "pending_steer" not in result
    assert result["metadata"]["harness"]["dead_end"] is True
    assert "Dead end: The required service is not available" in result["final_response"]


def test_recoverable_stale_dead_end_is_ignored_and_continues(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    from plugins.harness.continuation import handle_report_dead_end

    transform = _require_harness_final_transform(harness_hooks)
    harness_runner.start_run("Do not stop on stale ledger events", repo_root=git_repo)

    recorded = handle_report_dead_end(
        {
            "reason": "Stale sensor events from a prior compacted context window.",
            "blocker_ids": ["61"],
            "attempts": ["Reran tests"],
        }
    )
    recorded_payload = json.loads(recorded)
    assert recorded_payload["success"] is True
    assert recorded_payload["accepted"] is False

    result = _call_harness_transform(transform, "Done.")

    assert _transform_status(result) == "unverified"
    assert result["metadata"]["harness"]["dead_end"] is False
    assert "pending_steer" in result


def test_historical_tombstone_dead_end_is_recoverable(
    git_repo: Path,
    harness_runner,
):
    from plugins.harness.continuation import handle_report_dead_end

    harness_runner.start_run("Recover from historical tool tombstones", repo_root=git_repo)

    recorded = handle_report_dead_end(
        {
            "reason": (
                "Run is permanently blocked by 68 historical failed tool event "
                "tombstones satisfying generic.no_unresolved_failed_tools; "
                "these cannot be cleared or superseded across previous sessions."
            ),
            "blocker_ids": ["115-1083"],
            "attempts": ["Reran browser verification"],
        }
    )

    payload = json.loads(recorded)
    assert payload["success"] is True
    assert payload["accepted"] is False
    assert payload["recoverable"] is True


def test_resolve_blockers_tool_unblocks_stale_failures(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    from plugins.harness.continuation import handle_resolve_blockers

    transform = _require_harness_final_transform(harness_hooks)
    started = harness_runner.start_run("Resolve stale blocker events", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    failed = log.append("tool", {"event": "finished", "tool": "terminal", "ok": False})

    blocked = _call_harness_transform(transform, "Done.")
    assert str(failed.s) in blocked["metadata"]["harness"]["blocking"]

    resolved = handle_resolve_blockers(
        {
            "blocker_ids": [str(failed.s)],
            "reason": "Fresh validation superseded stale failure.",
            "evidence": "Focused tests now pass.",
        }
    )
    assert json.loads(resolved)["success"] is True

    transformed = _call_harness_transform(transform, "Done.")

    assert str(failed.s) not in transformed["metadata"]["harness"]["blocking"]


def test_resolve_blockers_tool_can_resolve_all_current_failures(
    git_repo: Path,
    harness_runner,
    harness_hooks,
):
    from plugins.harness.continuation import handle_resolve_blockers

    transform = _require_harness_final_transform(harness_hooks)
    started = harness_runner.start_run("Resolve all stale blockers", repo_root=git_repo)
    log = events.EventLog.for_run(started["run_id"], git_repo)
    failed_tool = log.append("tool", {"event": "finished", "tool": "browser", "ok": False})
    failed_sensor = log.append("sensor", {"id": "browser.verify", "ok": False})

    blocked = _call_harness_transform(transform, "Done.")
    assert str(failed_tool.s) in blocked["metadata"]["harness"]["blocking"]
    assert str(failed_sensor.s) in blocked["metadata"]["harness"]["blocking"]

    resolved = handle_resolve_blockers(
        {
            "resolve_all": True,
            "reason": "Fresh validation superseded historical browser timeouts.",
            "evidence": "Focused verification now passes.",
        }
    )
    payload = json.loads(resolved)

    assert payload["success"] is True
    assert payload["resolves"] == [failed_tool.s, failed_sensor.s]

    transformed = _call_harness_transform(transform, "Done.")

    assert str(failed_tool.s) not in transformed["metadata"]["harness"]["blocking"]
    assert str(failed_sensor.s) not in transformed["metadata"]["harness"]["blocking"]


def test_resolve_blockers_tool_accepts_event_ranges(
    git_repo: Path,
    harness_runner,
):
    from plugins.harness.continuation import handle_resolve_blockers

    harness_runner.start_run("Resolve blocker ranges", repo_root=git_repo)

    resolved = handle_resolve_blockers(
        {
            "blocker_ids": ["seq 115 through 117", "ev120-121"],
            "reason": "Fresh validation superseded historical failures.",
            "evidence": "All checks pass.",
        }
    )

    payload = json.loads(resolved)
    assert payload["success"] is True
    assert payload["resolves"] == [115, 116, 117, 120, 121]


def test_generic_final_response_helper_preserves_pending_steer(monkeypatch: pytest.MonkeyPatch):
    def fake_invoke_hook(hook_name: str, **kwargs: Any) -> list[Any]:
        assert hook_name == "transform_final_response"
        return [
            {
                "final_response": "not done",
                "completed": False,
                "partial": True,
                "pending_steer": "continue with verification",
            }
        ]

    monkeypatch.setattr(plugins, "invoke_hook", fake_invoke_hook)

    result = plugins.apply_final_response_transforms(
        final_response="original",
        completed=True,
        partial=False,
        session_id="session-1",
    )

    assert result["completed"] is False
    assert result["partial"] is True
    assert result["pending_steer"] == "continue with verification"


def test_generic_final_response_helper_preserves_harness_status_metadata(
    git_repo: Path,
    harness_runner,
    harness_hooks,
    monkeypatch: pytest.MonkeyPatch,
):
    transform = _require_harness_final_transform(harness_hooks)
    started = harness_runner.start_run("Preserve harness metadata", repo_root=git_repo)
    events.EventLog.for_run(started["run_id"], git_repo).append(
        "sensor",
        {
            "id": "tests.focused",
            "covers": ["manual-verification"],
            "ok": True,
        },
    )

    def fake_invoke_hook(hook_name: str, **kwargs: Any) -> list[Any]:
        assert hook_name == "transform_final_response"
        return [transform(**kwargs)]

    monkeypatch.setattr(plugins, "invoke_hook", fake_invoke_hook)

    result = plugins.apply_final_response_transforms(
        final_response="All done.",
        completed=True,
        partial=False,
        session_id="session-1",
        task_id="task-1",
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="pytest",
    )

    assert result["metadata"]["harness"]["run_id"] == started["run_id"]
    assert result["metadata"]["harness"]["status"] == "complete"
    assert _transform_status(result) == "complete"


def test_generic_final_response_helper_preserves_openspec_harness_metadata(
    git_repo: Path,
    harness_runner,
    harness_hooks,
    monkeypatch: pytest.MonkeyPatch,
):
    transform = _require_harness_final_transform(harness_hooks)
    change_dir = git_repo / "openspec" / "changes" / "final-open"
    change_dir.mkdir(parents=True)
    _install_fake_openspec(monkeypatch, acceptance_id="openspec-final")
    started = harness_runner.start_run("final-open", repo_root=git_repo)
    events.EventLog.for_run(started["run_id"], git_repo).append(
        "sensor",
        {
            "id": "tests.openspec",
            "covers": ["openspec-final"],
            "ok": True,
        },
    )

    def fake_invoke_hook(hook_name: str, **kwargs: Any) -> list[Any]:
        assert hook_name == "transform_final_response"
        return [transform(**kwargs)]

    monkeypatch.setattr(plugins, "invoke_hook", fake_invoke_hook)

    result = plugins.apply_final_response_transforms(
        final_response="All done.",
        completed=True,
        partial=False,
        session_id="session-1",
        task_id="task-1",
        interrupted=False,
        conversation_history=[],
        model="test-model",
        platform="pytest",
    )

    assert not (Path(started["run_path"]) / harness_runner.TASK_FILENAME).exists()
    assert result["metadata"]["harness"]["run_id"] == started["run_id"]
    assert result["metadata"]["harness"]["status"] == "complete"
    assert result["metadata"]["harness"]["complete_ids"] == ["openspec-final"]
    assert "Complete acceptance IDs: openspec-final" in result["final_response"]
