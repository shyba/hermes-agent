"""Provider-independent helpers for architecting harness task contracts."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

from .task import HarnessTask, HarnessTaskError, parse_task


ALLOWED_STATUSES = frozenset({"blocked", "complete", "partial", "unverified"})


class HarnessArchitectError(ValueError):
    """Raised when architect output cannot become a valid harness task."""


def build_architect_prompt(goal: str, context: str | None = None) -> str:
    """Build a deterministic prompt for an external model or human architect."""

    normalized_goal = _required_text(goal, "goal")
    normalized_context = context.strip() if isinstance(context, str) and context.strip() else "None provided."
    return "\n".join(
        [
            "Create a Hermes harness task contract for the goal below.",
            "",
            "Return only YAML or JSON matching this shape:",
            "version: '1'",
            "id: stable-kebab-case-id",
            "objective:",
            "  summary: concise objective summary",
            "intent:",
            "  latest_user_request: original user request",
            "acceptance_matrix:",
            "  - id: ac1",
            "    criterion: observable acceptance criterion",
            "    required_evidence: []",
            "finalization_policy:",
            "  complete_requires: [ac1]",
            "",
            "Rules:",
            "- Do not include prose outside the YAML or JSON.",
            "- Keep acceptance ids unique and stable.",
            "- If status is present, it must be one of: blocked, complete, partial, unverified.",
            "- If passes is present, it must reference acceptance_matrix ids.",
            "- finalization_policy.complete_requires must reference acceptance_matrix ids.",
            "",
            f"Goal: {normalized_goal}",
            "",
            "Context:",
            normalized_context,
        ]
    )


def deterministic_task_skeleton(
    task_id: str,
    goal: str,
    context: str | None = None,
) -> dict[str, Any]:
    """Generate a deterministic fallback task skeleton without model output."""

    normalized_id = _stable_id(task_id)
    normalized_goal = _required_text(goal, "goal")
    skeleton: dict[str, Any] = {
        "version": "1",
        "id": normalized_id,
        "objective": {
            "summary": normalized_goal,
        },
        "intent": {
            "latest_user_request": normalized_goal,
        },
        "acceptance_matrix": [
            {
                "id": "ac1",
                "criterion": "The requested work is implemented and verified against the stated goal.",
                "required_evidence": [],
            }
        ],
        "finalization_policy": {
            "complete_requires": ["ac1"],
        },
    }
    if isinstance(context, str) and context.strip():
        skeleton["context"] = context.strip()
    return skeleton


def parse_architect_output(output: str) -> dict[str, Any]:
    """Parse YAML, JSON, or fenced YAML/JSON architect output into a task dict."""

    text = _required_text(output, "output")
    payload = _extract_fenced_payload(text)
    data = _parse_payload(payload)
    validate_task_contract(data)
    return data


def validate_task_contract(data: dict[str, Any]) -> HarnessTask:
    """Validate architect-specific fields and the existing harness task contract."""

    try:
        parsed = parse_task(data)
        _validate_status(data)
        _validate_acceptance_references(data, set(parsed.acceptance_ids))
    except HarnessTaskError as exc:
        raise HarnessArchitectError(str(exc)) from exc
    return parsed


def _extract_fenced_payload(text: str) -> str:
    fence = re.search(
        r"```[ \t]*(?:yaml|yml|json)?[ \t]*(?:\r?\n)?(.*?)```",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if fence:
        return inspect.cleandoc(fence.group(1))
    return inspect.cleandoc(text)


def _parse_payload(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = _parse_yaml(payload)
    if not isinstance(data, dict):
        raise HarnessArchitectError("architect output must be a mapping")
    return data


def _parse_yaml(payload: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise HarnessArchitectError("PyYAML is required to parse architect YAML output") from exc
    try:
        return yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise HarnessArchitectError(f"invalid architect output: {exc}") from exc


def _validate_status(data: dict[str, Any]) -> None:
    status = data.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        raise HarnessArchitectError(
            "status must be one of: blocked, complete, partial, unverified"
        )


def _validate_acceptance_references(data: dict[str, Any], acceptance_ids: set[str]) -> None:
    _validate_string_references(data.get("passes"), "passes", acceptance_ids)

    policy = data.get("finalization_policy")
    if policy is None:
        return
    if not isinstance(policy, dict):
        raise HarnessArchitectError("finalization_policy must be a mapping when set")
    _validate_string_references(
        policy.get("complete_requires"),
        "finalization_policy.complete_requires",
        acceptance_ids,
    )


def _validate_string_references(value: Any, field: str, acceptance_ids: set[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        refs = [value]
    elif isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        refs = value
    else:
        raise HarnessArchitectError(f"{field} must be a string or list of strings")

    missing = sorted({item for item in refs if item not in acceptance_ids})
    if missing:
        raise HarnessArchitectError(
            f"{field} references unknown acceptance ids: {', '.join(missing)}"
        )


def _stable_id(value: str) -> str:
    text = _required_text(value, "task_id").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "manual-verification"


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessArchitectError(f"{field} must be a non-empty string")
    return value.strip()
