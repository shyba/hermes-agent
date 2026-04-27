"""Task contract parsing for deterministic harness runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HarnessTaskError(ValueError):
    """Raised when a harness task file does not match the contract."""


@dataclass(frozen=True)
class RequiredEvidence:
    sensor: str
    freshness: str | None = None


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    criterion: str
    required_evidence: tuple[RequiredEvidence, ...] = ()


@dataclass(frozen=True)
class HarnessTask:
    version: str
    id: str
    objective: dict[str, Any]
    intent: dict[str, Any]
    acceptance_matrix: tuple[AcceptanceCriterion, ...]
    constraints: Any = None
    workstreams: Any = None
    finalization_policy: Any = None
    raw: dict[str, Any] | None = None

    @property
    def acceptance_ids(self) -> list[str]:
        return [item.id for item in self.acceptance_matrix]


def load_task(path: str | Path) -> HarnessTask:
    """Load and validate a harness task from YAML."""

    yaml = _yaml()
    task_path = Path(path)
    try:
        data = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HarnessTaskError(f"could not read task file {task_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise HarnessTaskError(f"invalid YAML in task file {task_path}: {exc}") from exc
    return parse_task(data)


def parse_task(data: dict[str, Any]) -> HarnessTask:
    """Validate and normalize a harness task dictionary."""

    if not isinstance(data, dict):
        raise HarnessTaskError("task must be a mapping")

    version = _required_nonempty_string(data, "version")
    task_id = _required_nonempty_string(data, "id")
    objective = _required_mapping(data, "objective")
    intent = _required_mapping(data, "intent")

    if not _is_nonempty_string(objective.get("summary")):
        raise HarnessTaskError("objective.summary must be a non-empty string")
    if not _is_nonempty_string(intent.get("latest_user_request")):
        raise HarnessTaskError("intent.latest_user_request must be a non-empty string")

    matrix_raw = data.get("acceptance_matrix")
    if not isinstance(matrix_raw, list) or not matrix_raw:
        raise HarnessTaskError("acceptance_matrix must be a non-empty list")

    seen: set[str] = set()
    matrix: list[AcceptanceCriterion] = []
    for index, item in enumerate(matrix_raw):
        if not isinstance(item, dict):
            raise HarnessTaskError(f"acceptance_matrix[{index}] must be a mapping")
        criterion_id = _required_nonempty_string(item, "id", f"acceptance_matrix[{index}].id")
        if criterion_id in seen:
            raise HarnessTaskError(f"acceptance_matrix id must be unique: {criterion_id}")
        seen.add(criterion_id)

        criterion = _required_nonempty_string(
            item,
            "criterion",
            f"acceptance_matrix[{index}].criterion",
        )
        evidence = _parse_required_evidence(
            item.get("required_evidence", ()),
            f"acceptance_matrix[{index}].required_evidence",
        )
        matrix.append(
            AcceptanceCriterion(
                id=criterion_id,
                criterion=criterion,
                required_evidence=evidence,
            )
        )

    return HarnessTask(
        version=version,
        id=task_id,
        objective=dict(objective),
        intent=dict(intent),
        acceptance_matrix=tuple(matrix),
        constraints=data.get("constraints"),
        workstreams=data.get("workstreams"),
        finalization_policy=data.get("finalization_policy"),
        raw=dict(data),
    )


def _parse_required_evidence(value: Any, field: str) -> tuple[RequiredEvidence, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (RequiredEvidence(sensor=value),)
    if not isinstance(value, list):
        raise HarnessTaskError(f"{field} must be a list or legacy string")

    entries: list[RequiredEvidence] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if isinstance(item, str):
            entries.append(RequiredEvidence(sensor=item))
            continue
        if not isinstance(item, dict):
            raise HarnessTaskError(f"{item_field} must be a mapping or legacy string")
        sensor = _required_nonempty_string(item, "sensor", f"{item_field}.sensor")
        freshness = item.get("freshness")
        if freshness is not None and not _is_nonempty_string(freshness):
            raise HarnessTaskError(f"{item_field}.freshness must be a non-empty string when set")
        entries.append(RequiredEvidence(sensor=sensor, freshness=freshness))
    return tuple(entries)


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise HarnessTaskError(f"{key} must be a mapping")
    return value


def _required_nonempty_string(data: dict[str, Any], key: str, field: str | None = None) -> str:
    value = data.get(key)
    if not _is_nonempty_string(value):
        raise HarnessTaskError(f"{field or key} must be a non-empty string")
    return value


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise HarnessTaskError("PyYAML is required to load harness task files") from exc
    return yaml
