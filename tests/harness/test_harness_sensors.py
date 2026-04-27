"""Contract tests for harness ledger sensors."""

from __future__ import annotations

from pathlib import Path

from plugins.harness.events import EventLog, read_events
from plugins.harness.sensors import (
    SensorResult,
    run_all_ledger_sensors,
    run_ledger_sensor,
)
from plugins.harness.task import load_task


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness"


def _task():
    return load_task(FIXTURE_ROOT / "task-complete.yaml")


def _events(tmp_path):
    return EventLog(tmp_path / "events.jsonl")


def test_verification_freshness_passes_with_ok_sensor_after_last_mutation(tmp_path):
    log = _events(tmp_path)
    log.append("tool", {"name": "edit", "fx": "mutate", "ok": True})
    evidence = log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    result = run_ledger_sensor("generic.verification_freshness", _task(), log.read_all())

    assert isinstance(result, SensorResult)
    assert result.ok is True
    assert result.covers == ("ac1", "ac2")
    assert result.blocking == ()
    assert result.evidence_event == evidence.s


def test_verification_freshness_requires_declared_evidence_sensor_id(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "other.sensor", "covers": ["ac1", "ac2"], "ok": True})

    result = run_ledger_sensor("generic.verification_freshness", _task(), log.read_all())

    assert result.ok is False
    assert result.blocking == ("ac1", "ac2")


def test_verification_freshness_fails_when_sensor_evidence_is_stale(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})
    log.append("tool", {"name": "edit", "fx": "mutate", "ok": True})

    result = run_ledger_sensor("generic.verification_freshness", _task(), log.read_all())

    assert result.ok is False
    assert result.covers == ()
    assert result.blocking == ("ac1", "ac2")


def test_claims_have_evidence_does_not_require_freshness_after_mutation(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})
    log.append("tool", {"name": "edit", "fx": "mutate", "ok": True})

    result = run_ledger_sensor("generic.claims_have_evidence", _task(), log.read_all())

    assert result.ok is True
    assert result.covers == ("ac1", "ac2")


def test_unresolved_failed_tool_delegate_and_sensor_block_sensors(tmp_path):
    log = _events(tmp_path)
    tool = log.append("tool", {"name": "shell", "ok": False})
    delegate = log.append("delegate", {"name": "worker-a", "status": "error", "required": True, "ok": False})
    sensor = log.append("sensor", {"id": "tests.focused", "ok": False})

    unresolved = run_ledger_sensor("generic.no_unresolved_failed_tools", _task(), log.read_all())
    no_timeout = run_ledger_sensor("generic.no_done_after_timeout", _task(), log.read_all())

    assert unresolved.ok is False
    assert unresolved.blocking == (str(tool.s), str(delegate.s), str(sensor.s))
    assert no_timeout.ok is False
    assert no_timeout.blocking == (str(delegate.s),)


def test_resolved_failed_events_do_not_block_sensors(tmp_path):
    log = _events(tmp_path)
    failed = log.append("tool", {"name": "shell", "ok": False})
    log.append("tool", {"name": "retry", "ok": True, "resolves": failed.s})

    result = run_ledger_sensor("generic.no_unresolved_failed_tools", _task(), log.read_all())

    assert result.ok is True
    assert result.blocking == ()


def test_resolution_event_resolves_stale_failed_events(tmp_path):
    log = _events(tmp_path)
    failed_tool = log.append("tool", {"name": "shell", "event": "finished", "ok": False})
    failed_sensor = log.append("sensor", {"id": "tests.focused", "ok": False})
    log.append(
        "resolution",
        {
            "event": "resolved_blockers",
            "resolves": [failed_tool.s, failed_sensor.s],
            "reason": "fresh validation superseded stale failures",
            "ok": True,
        },
    )

    result = run_ledger_sensor("generic.no_unresolved_failed_tools", _task(), log.read_all())

    assert result.ok is True
    assert result.blocking == ()


def test_resolved_required_delegate_timeout_does_not_block_sensor(tmp_path):
    log = _events(tmp_path)
    failed = log.append("delegate", {"name": "worker-a", "status": "timeout", "required": True, "ok": False})
    log.append("delegate", {"name": "worker-b", "status": "complete", "required": True, "ok": True, "resolves": failed.s})

    result = run_ledger_sensor("generic.no_done_after_timeout", _task(), log.read_all())

    assert result.ok is True
    assert result.blocking == ()


def test_read_only_policy_violation_blocks_sensor(tmp_path):
    log = _events(tmp_path)
    violation = log.append(
        "policy",
        {"policy": "read_only", "action": "violation", "path": "run_agent.py", "ok": False},
    )

    result = run_ledger_sensor("generic.no_read_only_violation", _task(), log.read_all())

    assert result.ok is False
    assert result.blocking == (str(violation.s),)
    assert result.evidence_event == violation.s


def test_resolved_read_only_policy_violation_does_not_block_sensor(tmp_path):
    log = _events(tmp_path)
    violation = log.append(
        "policy",
        {"policy": "read_only", "action": "violation", "path": "run_agent.py", "ok": False},
    )
    log.append("policy", {"policy": "read_only", "action": "waived", "ok": True, "resolves": violation.s})

    result = run_ledger_sensor("generic.no_read_only_violation", _task(), log.read_all())

    assert result.ok is True
    assert result.blocking == ()


def test_run_all_ledger_sensors_accepts_explicit_sensor_ids(tmp_path):
    log = _events(tmp_path)
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})

    results = run_all_ledger_sensors(
        _task(),
        list(read_events(log.path)),
        sensor_ids=["generic.claims_have_evidence", "generic.no_read_only_violation"],
    )

    assert [result.id for result in results] == [
        "generic.claims_have_evidence",
        "generic.no_read_only_violation",
    ]
    assert [result.ok for result in results] == [True, True]
