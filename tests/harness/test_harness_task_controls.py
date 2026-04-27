"""Contract tests for harness task and control parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.harness.controls import (
    HarnessControlsError,
    default_controls,
    load_controls,
    parse_controls,
)
from plugins.harness.task import HarnessTaskError, load_task, parse_task


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness"


def test_complete_and_blocked_task_fixtures_parse():
    complete = load_task(FIXTURE_ROOT / "task-complete.yaml")
    blocked = load_task(FIXTURE_ROOT / "task-blocked.yaml")

    assert complete.id == "harness-fixture-complete"
    assert complete.acceptance_ids == ["ac1", "ac2"]
    assert blocked.id == "harness-fixture-blocked"
    assert blocked.acceptance_ids == ["ac1"]


def test_controls_fixture_parses():
    controls = load_controls(FIXTURE_ROOT / "controls.yaml")

    assert controls.version == "1"
    assert controls.sensor_ids == ["tests.focused"]
    sensor = controls.sensors[0]
    assert sensor.kind == "command"
    assert sensor.command == "scripts/run_tests.sh tests/plugins/test_harness_plugin.py tests/harness/test_harness_events.py"
    assert sensor.covers == ("ac1", "ac2")


def test_task_validation_rejects_duplicate_acceptance_ids():
    data = {
        "version": "1",
        "id": "duplicate-ac",
        "objective": {"summary": "Reject duplicate acceptance ids."},
        "intent": {"latest_user_request": "Validate the task contract."},
        "acceptance_matrix": [
            {"id": "ac1", "criterion": "First criterion.", "required_evidence": []},
            {"id": "ac1", "criterion": "Duplicate criterion.", "required_evidence": []},
        ],
    }

    with pytest.raises(HarnessTaskError, match="unique"):
        parse_task(data)


def test_task_parsing_normalizes_structured_required_evidence():
    task = parse_task(
        {
            "version": "1",
            "id": "structured-evidence",
            "objective": {"summary": "Normalize structured evidence."},
            "intent": {"latest_user_request": "Validate evidence contract."},
            "acceptance_matrix": [
                {
                    "id": "ac1",
                    "criterion": "Structured evidence is preserved.",
                    "required_evidence": [
                        {"sensor": "tests.focused", "freshness": "after_last_mutation"}
                    ],
                }
            ],
        }
    )

    evidence = task.acceptance_matrix[0].required_evidence[0]
    assert evidence.sensor == "tests.focused"
    assert evidence.freshness == "after_last_mutation"


@pytest.mark.parametrize(
    "missing",
    ["version", "id", "objective", "intent", "acceptance_matrix"],
)
def test_task_validation_rejects_missing_required_fields(missing):
    data = {
        "version": "1",
        "id": "missing-field",
        "objective": {"summary": "Reject missing required fields."},
        "intent": {"latest_user_request": "Validate the task contract."},
        "acceptance_matrix": [{"id": "ac1", "criterion": "A criterion."}],
    }
    data.pop(missing)

    with pytest.raises(HarnessTaskError):
        parse_task(data)


def test_default_controls_include_the_five_ledger_sensors():
    controls = default_controls()

    assert controls.sensor_ids == [
        "generic.claims_have_evidence",
        "generic.verification_freshness",
        "generic.no_done_after_timeout",
        "generic.no_read_only_violation",
        "generic.no_unresolved_failed_tools",
    ]
    assert all(sensor.kind == "ledger" for sensor in controls.sensors)
    assert all(sensor.required is True for sensor in controls.sensors)


def test_control_validation_rejects_invalid_shapes():
    with pytest.raises(HarnessControlsError):
        parse_controls({"version": "1", "sensors": []})

    with pytest.raises(HarnessControlsError, match="unique"):
        parse_controls(
            {
                "version": "1",
                "sensors": [
                    {"id": "generic.verification_freshness"},
                    {"id": "generic.verification_freshness"},
                ],
            }
        )
