"""Assignment planning tests for harness manager slices."""

from __future__ import annotations

from plugins.harness.controls import parse_controls
from plugins.harness.manager import plan_assignments
from plugins.harness.task import parse_task


def _task(**overrides):
    data = {
        "version": "1",
        "id": "manager-plan",
        "objective": {"summary": "Plan workstream assignments."},
        "intent": {"latest_user_request": "Split this task across workers."},
        "acceptance_matrix": [
            {"id": "ac-code", "criterion": "Code path is updated.", "required_evidence": []},
            {"id": "ac-tests", "criterion": "Tests cover the behavior.", "required_evidence": []},
            {"id": "ac-docs", "criterion": "Docs are checked.", "required_evidence": []},
        ],
    }
    data.update(overrides)
    return parse_task(data)


def test_plan_assignments_splits_multiple_workstreams_with_stable_ids():
    task = _task(
        roles={"architect": "lead-a", "manager": "lead-m"},
        workstreams=[
            {"id": "core-api", "owner": "worker-a", "acceptance_ids": ["ac-code"]},
            {"id": "tests", "owner": "worker-b", "acceptance_ids": ["ac-tests"]},
        ],
    )
    controls = parse_controls(
        {
            "version": "1",
            "sensors": [
                {"id": "tests.core", "kind": "command", "covers": ["ac-code"]},
                {"id": "tests.focused", "kind": "command", "covers": ["ac-tests"]},
            ],
        }
    )

    assignments = plan_assignments(task, controls)

    assert [item.id for item in assignments] == [
        "assign-manager-plan-architect",
        "assign-manager-plan-manager",
        "assign-manager-plan-core-api-coder",
        "assign-manager-plan-tests-coder",
    ]
    assert [(item.role, item.assignee) for item in assignments[:2]] == [
        ("architect", "lead-a"),
        ("manager", "lead-m"),
    ]
    assert assignments[2].acceptance_ids == ("ac-code",)
    assert assignments[2].sensors == ("tests.core",)
    assert assignments[2].can_mutate is True
    assert assignments[3].acceptance_ids == ("ac-tests",)
    assert assignments[3].sensors == ("tests.focused",)
    assert assignments[3].can_mutate is True


def test_plan_assignments_keeps_blocked_workstreams_last():
    task = _task(
        workstreams=[
            {"id": "blocked-first", "owner": "worker-a", "blocked": True, "acceptance_ids": ["ac-code"]},
            {"id": "ready-second", "owner": "worker-b", "acceptance_ids": ["ac-tests"]},
        ],
    )

    assignments = plan_assignments(task)

    assert [item.workstream_id for item in assignments] == ["ready-second", "blocked-first"]
    assert assignments[-1].blocked is True
    assert "blocked" in assignments[-1].responsibilities


def test_plan_assignments_creates_reviewer_slice_when_requested():
    task = _task(
        workstreams=[
            {
                "id": "reviewed",
                "owner": "worker-a",
                "reviewer": "reviewer-a",
                "acceptance_ids": ["ac-code"],
            }
        ],
    )

    assignments = plan_assignments(task)

    assert [item.role for item in assignments] == ["coder", "reviewer"]
    reviewer = assignments[1]
    assert reviewer.id == "assign-manager-plan-reviewed-reviewer"
    assert reviewer.assignee == "reviewer-a"
    assert reviewer.can_mutate is False
    assert "read_only" in reviewer.responsibilities


def test_plan_assignments_disables_mutation_when_read_only_controls_apply():
    task = _task(
        intent={"latest_user_request": "Audit without changes.", "read_only": True},
        workstreams=[
            {"id": "audit", "owner": "worker-a", "acceptance_ids": ["ac-docs"]},
        ],
    )
    controls = parse_controls(
        {
            "version": "1",
            "sensors": [
                {"id": "generic.no_read_only_violation", "kind": "ledger", "covers": ["ac-docs"]},
            ],
        }
    )

    assignments = plan_assignments(task, controls)

    assert len(assignments) == 1
    assert assignments[0].role == "coder"
    assert assignments[0].can_mutate is False
    assert "read_only" in assignments[0].responsibilities
    assert "mutate" not in assignments[0].responsibilities


def test_plan_assignments_scopes_read_only_sensor_to_covered_acceptance_ids():
    task = _task(
        workstreams=[
            {"id": "code", "owner": "worker-a", "acceptance_ids": ["ac-code"]},
            {"id": "docs", "owner": "worker-b", "acceptance_ids": ["ac-docs"]},
        ],
    )
    controls = parse_controls(
        {
            "version": "1",
            "sensors": [
                {
                    "id": "docs.read_only",
                    "kind": "ledger",
                    "covers": ["ac-docs"],
                    "config": {"read_only": True},
                },
            ],
        }
    )

    assignments = plan_assignments(task, controls)

    by_workstream = {item.workstream_id: item for item in assignments}
    assert by_workstream["code"].sensors == ()
    assert by_workstream["code"].can_mutate is True
    assert by_workstream["docs"].sensors == ("docs.read_only",)
    assert by_workstream["docs"].can_mutate is False
