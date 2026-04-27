"""Contract tests for harness finalization."""

from __future__ import annotations

from pathlib import Path

from plugins.harness.events import EventLog, read_events
from plugins.harness.finalization import FinalizationResult, evaluate
from plugins.harness.task import load_task


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness"


def _task(name: str = "task-complete.yaml"):
    return load_task(FIXTURE_ROOT / name)


def _events(tmp_path):
    return EventLog(tmp_path / "events.jsonl")


def test_complete_fixture_finalizes_complete():
    result = evaluate(
        _task(),
        list(read_events(FIXTURE_ROOT / "events-complete.jsonl")),
        sensor_ids=["generic.claims_have_evidence", "generic.no_unresolved_failed_tools"],
    )

    assert isinstance(result, FinalizationResult)
    assert result.status == "complete"
    assert result.complete_ids == ("ac1", "ac2")
    assert result.missing_ids == ()
    assert result.blocking == ()


def test_blocked_fixture_finalizes_blocked():
    result = evaluate(
        _task("task-blocked.yaml"),
        list(read_events(FIXTURE_ROOT / "events-blocked.jsonl")),
        sensor_ids=["generic.no_unresolved_failed_tools"],
    )

    assert result.status == "blocked"
    assert result.complete_ids == ()
    assert result.missing_ids == ("ac1",)
    assert result.blocking == ("2",)


def test_finalization_status_complete(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all(), sensor_ids=["generic.verification_freshness"])

    assert result.status == "complete"
    assert result.complete_ids == ("ac1", "ac2")
    assert result.missing_ids == ()
    assert result.blocking == ()


def test_finalization_requires_declared_evidence_sensor_id(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "other.sensor", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "unverified"
    assert result.complete_ids == ()
    assert result.missing_ids == ("ac1", "ac2")


def test_finalization_policy_complete_requires_can_narrow_acceptance_matrix(tmp_path):
    task = _task()
    task = type(task)(
        version=task.version,
        id=task.id,
        objective=task.objective,
        intent=task.intent,
        acceptance_matrix=task.acceptance_matrix,
        constraints=task.constraints,
        workstreams=task.workstreams,
        finalization_policy={"complete_requires": ["ac1"]},
        raw=task.raw,
    )
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1"], "ok": True})

    result = evaluate(task, log.read_all())

    assert result.status == "complete"
    assert result.complete_ids == ("ac1",)
    assert result.missing_ids == ()


def test_finalization_status_partial(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "partial"
    assert result.complete_ids == ("ac1",)
    assert result.missing_ids == ("ac2",)
    assert result.blocking == ()


def test_finalization_status_blocked_by_failed_tool_and_policy(tmp_path):
    log = _events(tmp_path)
    failed = log.append("tool", {"name": "shell", "mutating": False, "ok": False})
    policy = log.append("policy", {"policy": "read_only", "action": "violation", "ok": False})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(
        _task(),
        log.read_all(),
        sensor_ids=[
            "generic.verification_freshness",
            "generic.no_unresolved_failed_tools",
            "generic.no_read_only_violation",
        ],
    )

    assert result.status == "blocked"
    assert result.complete_ids == ("ac1", "ac2")
    assert result.missing_ids == ()
    assert result.blocking == (str(failed.s), str(policy.s))


def test_resolved_failed_tool_does_not_block_finalization(tmp_path):
    log = _events(tmp_path)
    failed = log.append("tool", {"name": "shell", "fx": "verify", "ok": False})
    log.append("tool", {"name": "retry", "fx": "verify", "ok": True, "resolves": failed.s})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "complete"
    assert result.blocking == ()


def test_resolved_required_delegate_timeout_does_not_block_finalization(tmp_path):
    log = _events(tmp_path)
    failed = log.append("delegate", {"name": "worker-a", "status": "timeout", "required": True, "ok": False})
    log.append("delegate", {"name": "worker-b", "status": "complete", "required": True, "ok": True, "resolves": failed.s})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "complete"
    assert result.blocking == ()


def test_resolved_read_only_policy_violation_does_not_block_finalization(tmp_path):
    log = _events(tmp_path)
    violation = log.append("policy", {"policy": "read_only", "action": "violation", "ok": False})
    log.append("policy", {"policy": "read_only", "action": "waived", "ok": True, "resolves": violation.s})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "complete"
    assert result.blocking == ()


def test_finalization_status_unverified_without_sensor_evidence(tmp_path):
    log = _events(tmp_path)
    log.append("run", {"event": "created", "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "unverified"
    assert result.complete_ids == ()
    assert result.missing_ids == ("ac1", "ac2")
    assert result.blocking == ()


def test_non_mutating_tool_does_not_make_later_evidence_stale(tmp_path):
    log = _events(tmp_path)
    log.append("tool", {"name": "read_file", "fx": "read", "ok": True})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "complete"
    assert result.complete_ids == ("ac1", "ac2")
    assert result.blocking == ()


def test_sensor_without_after_can_verify_after_mutating_tool(tmp_path):
    log = _events(tmp_path)
    log.append("tool", {"name": "edit_file", "fx": "mutate", "ok": True})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "complete"
    assert result.complete_ids == ("ac1", "ac2")
    assert result.missing_ids == ()
    assert result.blocking == ()


def test_default_finalization_runs_hard_blocking_ledger_sensors(tmp_path):
    log = _events(tmp_path)
    failed = log.append("tool", {"name": "shell", "fx": "verify", "ok": False})
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = evaluate(_task(), log.read_all())

    assert result.status == "blocked"
    assert str(failed.s) in result.blocking
    assert result.sensor_results
