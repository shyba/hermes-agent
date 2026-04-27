"""OpenSpec reader tests for harness tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.harness.openspec import load_openspec_change
from plugins.harness.task import HarnessTaskError


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "openspec"


def test_valid_openspec_change_derives_harness_task():
    task = load_openspec_change(FIXTURE_ROOT / "valid-change")

    assert task.id == "valid-change"
    assert task.objective["summary"] == "Add a provider-independent OpenSpec reader."
    assert (
        task.intent["latest_user_request"]
        == "Harness tasks should be derivable from OpenSpec change folders."
    )
    assert task.acceptance_ids == [
        "harness-openspec-reader-loads-change-folders",
        "harness-openspec-reader-reports-validation-errors",
    ]
    assert task.finalization_policy == {
        "complete_requires": [
            "harness-openspec-reader-loads-change-folders",
            "harness-openspec-reader-reports-validation-errors",
        ]
    }
    assert task.raw["source_format"] == "openspec"
    assert task.raw["change_id"] == "valid-change"
    assert task.raw["paths"]["proposal"].endswith("proposal.md")
    assert task.raw["paths"]["tasks"].endswith("tasks.md")
    assert task.raw["paths"]["design"].endswith("design.md")
    assert task.raw["capability_files"]["harness-openspec-reader"].endswith("spec.md")

    evidence = task.acceptance_matrix[0].required_evidence[0]
    assert evidence.sensor == "openspec.completion_gate"
    assert evidence.freshness == "after_last_mutation"
    assert task.raw["openspec"]["completion_gate"][0]["text"] == "focused parser tests pass"


def test_multiple_requirements_and_scenarios_become_acceptance_criteria():
    task = load_openspec_change(FIXTURE_ROOT / "valid-change")

    first = task.acceptance_matrix[0]
    assert first.criterion.startswith("Loads change folders: The system SHALL derive")
    assert "Valid folder - Given proposal.md" in first.criterion
    assert "Multiple scenarios - Given a spec with two Scenario blocks" in first.criterion

    second = task.acceptance_matrix[1]
    assert second.criterion.startswith("Reports validation errors:")
    assert "Missing specs - Given proposal.md and tasks.md but no specs directory" in second.criterion


def test_completion_gate_ids_must_map_to_acceptance_ids():
    with pytest.raises(HarnessTaskError, match="completion gate ids"):
        load_openspec_change(FIXTURE_ROOT / "unknown-gate")


def test_missing_specs_errors():
    with pytest.raises(HarnessTaskError, match="missing specs"):
        load_openspec_change(FIXTURE_ROOT / "missing-specs")


def test_requirement_body_must_include_normative_text():
    with pytest.raises(HarnessTaskError, match="SHALL or MUST"):
        load_openspec_change(FIXTURE_ROOT / "no-normative")


def test_delta_sections_preserve_metadata_and_filter_removed():
    task = load_openspec_change(FIXTURE_ROOT / "delta-change")

    assert task.acceptance_ids == [
        "demo-added-behavior",
        "demo-modified-behavior",
    ]
    assert task.finalization_policy == {
        "complete_requires": [
            "demo-added-behavior",
            "demo-modified-behavior",
        ]
    }
    raw_by_id = {item["id"]: item for item in task.raw["openspec"]["requirements"]}
    assert raw_by_id["demo-added-behavior"]["delta_type"] == "ADDED"
    assert raw_by_id["demo-modified-behavior"]["delta_type"] == "MODIFIED"
    assert raw_by_id["demo-removed-behavior"]["delta_type"] == "REMOVED"
    assert "demo-removed-behavior" not in task.acceptance_ids


def test_partial_completion_gate_does_not_reduce_required_scope():
    task = load_openspec_change(FIXTURE_ROOT / "partial-gate")

    assert task.acceptance_ids == ["demo-first", "demo-second"]
    assert task.finalization_policy == {"complete_requires": ["demo-first", "demo-second"]}
    assert task.acceptance_matrix[0].required_evidence[0].sensor == "openspec.completion_gate"
    assert task.acceptance_matrix[1].required_evidence == ()


def test_missing_completion_gate_does_not_parse_normal_checklist_tasks_as_gates():
    task = load_openspec_change(FIXTURE_ROOT / "no-completion-gate")

    assert task.acceptance_ids == ["demo-first", "demo-second"]
    assert task.finalization_policy == {"complete_requires": ["demo-first", "demo-second"]}
    assert task.raw["openspec"]["completion_gate"] == []
    assert task.acceptance_matrix[0].required_evidence == ()
    assert task.acceptance_matrix[1].required_evidence == ()
    assert task.raw["openspec"]["tasks"] == [
        {"checked": False, "text": "demo-first: implement parser"},
        {"checked": True, "text": "demo-second: add regression coverage"},
    ]


def test_tasks_checklist_is_preserved_with_checked_state():
    task = load_openspec_change(FIXTURE_ROOT / "valid-change")

    assert task.raw["openspec"]["tasks"] == [
        {"checked": True, "text": "Create parser."},
        {
            "checked": False,
            "text": "harness-openspec-reader-loads-change-folders: focused parser tests pass",
        },
        {
            "checked": True,
            "text": "harness-openspec-reader-reports-validation-errors: validation tests pass",
        },
    ]


def test_free_text_completion_gate_is_not_treated_as_sensor_id():
    task = load_openspec_change(FIXTURE_ROOT / "free-text-gate")

    evidence = task.acceptance_matrix[0].required_evidence[0]
    assert evidence.sensor == "openspec.completion_gate"
    assert task.raw["openspec"]["completion_gate"][0]["text"] == "focused parser tests pass"


def test_scenarios_are_case_insensitive_and_preserve_steps():
    task = load_openspec_change(FIXTURE_ROOT / "scenario-steps")

    scenario = task.raw["openspec"]["requirements"][0]["scenarios"][0]
    assert scenario == {
        "title": "Mixed case",
        "steps": [
            "given initial state",
            "WHEN the parser runs",
            "Then structured steps are preserved.",
        ],
        "summary": "Mixed case - given initial state WHEN the parser runs Then structured steps are preserved.",
    }
