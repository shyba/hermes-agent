"""Tests for provider-independent harness task architecture helpers."""

from __future__ import annotations

import pytest

from plugins.harness.architect import (
    HarnessArchitectError,
    build_architect_prompt,
    deterministic_task_skeleton,
    parse_architect_output,
    validate_task_contract,
)
from plugins.harness.task import parse_task


def test_deterministic_skeleton_is_stable_and_task_compatible():
    first = deterministic_task_skeleton("Ship Wave 4 Slice A!", "Add architect helpers")
    second = deterministic_task_skeleton("Ship Wave 4 Slice A!", "Add architect helpers")

    assert first == second
    assert first["id"] == "ship-wave-4-slice-a"
    assert first["objective"]["summary"] == "Add architect helpers"
    assert first["intent"]["latest_user_request"] == "Add architect helpers"
    assert first["finalization_policy"]["complete_requires"] == ["ac1"]

    parsed = parse_task(first)
    assert parsed.id == "ship-wave-4-slice-a"
    assert parsed.acceptance_ids == ["ac1"]


def test_build_architect_prompt_contains_goal_context_and_contract_requirements():
    prompt = build_architect_prompt("Create a task file", context="Use command sensors.")

    assert "Create a task file" in prompt
    assert "Use command sensors." in prompt
    assert "acceptance_matrix" in prompt
    assert "finalization_policy.complete_requires" in prompt


def test_parse_fenced_yaml_output():
    parsed = parse_architect_output(
        """
        ```yaml
        version: '1'
        id: focused-harness-task
        objective:
          summary: Add a focused harness task.
        intent:
          latest_user_request: Add a focused harness task.
        status: unverified
        passes:
          - ac1
        acceptance_matrix:
          - id: ac1
            criterion: The task parses through the harness task contract.
            required_evidence:
              - sensor: tests.focused
                freshness: after_last_mutation
        finalization_policy:
          complete_requires:
            - ac1
        ```
        """
    )

    task = validate_task_contract(parsed)

    assert parsed["id"] == "focused-harness-task"
    assert task.acceptance_ids == ["ac1"]
    assert task.acceptance_matrix[0].required_evidence[0].sensor == "tests.focused"


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("[]", "mapping"),
        (
            """
            version: '1'
            id: invalid-status
            objective:
              summary: Reject invalid status.
            intent:
              latest_user_request: Reject invalid status.
            status: done
            acceptance_matrix:
              - id: ac1
                criterion: Status must use harness finalization vocabulary.
                required_evidence: []
            """,
            "status",
        ),
        (
            """
            version: '1'
            id: invalid-reference
            objective:
              summary: Reject unknown references.
            intent:
              latest_user_request: Reject unknown references.
            passes:
              - ac-missing
            acceptance_matrix:
              - id: ac1
                criterion: References must target acceptance ids.
                required_evidence: []
            """,
            "unknown acceptance ids",
        ),
    ],
)
def test_invalid_output_errors(payload, match):
    with pytest.raises(HarnessArchitectError, match=match):
        parse_architect_output(payload)


def test_parse_json_output_is_compatible_with_existing_task_parser():
    parsed = parse_architect_output(
        """
        {
          "version": "1",
          "id": "json-task",
          "objective": {"summary": "Parse JSON architect output."},
          "intent": {"latest_user_request": "Parse JSON architect output."},
          "acceptance_matrix": [
            {
              "id": "ac1",
              "criterion": "JSON output is accepted.",
              "required_evidence": []
            }
          ],
          "finalization_policy": {"complete_requires": "ac1"}
        }
        """
    )

    existing = parse_task(parsed)
    architect = validate_task_contract(parsed)

    assert architect == existing
    assert architect.acceptance_ids == ["ac1"]
