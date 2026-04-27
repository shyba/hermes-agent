"""Provider-independent assignment planning for harness managers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_MANAGER_ROLES = ("architect", "manager")
_WORKSTREAM_ROLES = ("coder", "reviewer")
_READ_ONLY_SENSOR_IDS = frozenset({
    "generic.git_status_clean",
})


@dataclass(frozen=True)
class AssignmentSlice:
    id: str
    role: str
    workstream_id: str | None
    assignee: str | None
    acceptance_ids: tuple[str, ...]
    sensors: tuple[str, ...]
    responsibilities: tuple[str, ...]
    can_mutate: bool
    blocked: bool = False


def plan_assignments(task: Any, controls: Any = None) -> tuple[AssignmentSlice, ...]:
    """Build deterministic manager-ready assignment slices without calling delegates."""

    global_sensors = _global_control_sensor_ids(controls)
    read_only = _is_read_only_task(task) or _has_global_read_only_controls(controls)

    assignments: list[tuple[int, int, AssignmentSlice]] = []
    sequence = 0

    for role in _declared_roles(task, _MANAGER_ROLES):
        assignments.append(
            (
                0,
                sequence,
                _assignment(
                    task=task,
                    role=role,
                    workstream_id=None,
                    assignee=_role_assignee(task, role),
                    acceptance_ids=_acceptance_ids(task),
                    sensors=global_sensors,
                    can_mutate=False,
                    blocked=False,
                ),
            )
        )
        sequence += 1

    workstreams = _workstreams(task)
    if not workstreams:
        workstreams = ({"id": "default", "acceptance_ids": _acceptance_ids(task)},)

    for index, workstream in enumerate(workstreams):
        workstream_id = _workstream_id(workstream, index)
        acceptance_ids = _workstream_acceptance_ids(workstream, task)
        sensors = _workstream_sensors(workstream, controls, acceptance_ids, global_sensors)
        blocked = _is_blocked(workstream)
        sort_group = 2 if blocked else 1

        for role in _workstream_roles(workstream):
            assignments.append(
                (
                    sort_group,
                    sequence,
                    _assignment(
                        task=task,
                        role=role,
                        workstream_id=workstream_id,
                        assignee=_workstream_assignee(workstream, role),
                        acceptance_ids=acceptance_ids,
                        sensors=sensors,
                        can_mutate=role == "coder" and not read_only and not _read_only_sensors_only(sensors, controls),
                        blocked=blocked,
                    ),
                )
            )
            sequence += 1

    return tuple(item for _, _, item in sorted(assignments, key=lambda item: (item[0], item[1])))


def _assignment(
    *,
    task: Any,
    role: str,
    workstream_id: str | None,
    assignee: str | None,
    acceptance_ids: tuple[str, ...],
    sensors: tuple[str, ...],
    can_mutate: bool,
    blocked: bool,
) -> AssignmentSlice:
    responsibilities = ["plan" if role in _MANAGER_ROLES else role]
    responsibilities.append("mutate" if can_mutate else "read_only")
    if sensors:
        responsibilities.append("collect_evidence")
    if blocked:
        responsibilities.append("blocked")

    return AssignmentSlice(
        id=_assignment_id(_task_id(task), workstream_id, role),
        role=role,
        workstream_id=workstream_id,
        assignee=assignee,
        acceptance_ids=acceptance_ids,
        sensors=sensors,
        responsibilities=tuple(responsibilities),
        can_mutate=can_mutate,
        blocked=blocked,
    )


def _declared_roles(task: Any, allowed: tuple[str, ...]) -> tuple[str, ...]:
    raw = _value(task, "roles")
    if raw is None:
        raw = _mapping_value(_value(task, "intent"), "roles")

    roles: list[str] = []
    if isinstance(raw, dict):
        candidates = raw.keys()
    elif isinstance(raw, list | tuple):
        candidates = raw
    else:
        candidates = ()
    for item in candidates:
        if isinstance(item, str) and item in allowed and item not in roles:
            roles.append(item)
    return tuple(roles)


def _role_assignee(task: Any, role: str) -> str | None:
    raw = _value(task, "roles")
    if not isinstance(raw, dict):
        raw = _mapping_value(_value(task, "intent"), "roles")
    if isinstance(raw, dict):
        return _assignee(raw.get(role))
    return None


def _workstreams(task: Any) -> tuple[Any, ...]:
    value = _value(task, "workstreams")
    if isinstance(value, list | tuple):
        return tuple(value)
    if isinstance(value, dict):
        return tuple(dict(item, id=key) if isinstance(item, dict) else {"id": key, "value": item} for key, item in value.items())
    return ()


def _workstream_roles(workstream: Any) -> tuple[str, ...]:
    roles: list[str] = []
    raw = _mapping_value(workstream, "roles")
    if isinstance(raw, dict):
        candidates = raw.keys()
    elif isinstance(raw, list | tuple):
        candidates = raw
    else:
        candidates = ("coder",)
    for role in candidates:
        if isinstance(role, str) and role in _WORKSTREAM_ROLES and role not in roles:
            roles.append(role)
    if _mapping_value(workstream, "reviewer") is not None or _mapping_value(workstream, "reviewers") is not None:
        if "reviewer" not in roles:
            roles.append("reviewer")
    return tuple(roles or ("coder",))


def _workstream_assignee(workstream: Any, role: str) -> str | None:
    roles = _mapping_value(workstream, "roles")
    if isinstance(roles, dict) and role in roles:
        return _assignee(roles.get(role))
    if role == "coder":
        return _assignee(_mapping_value(workstream, "owner"))
    if role == "reviewer":
        return _assignee(_mapping_value(workstream, "reviewer")) or _assignee(_mapping_value(workstream, "reviewers"))
    return None


def _workstream_acceptance_ids(workstream: Any, task: Any) -> tuple[str, ...]:
    value = _mapping_value(workstream, "acceptance_ids")
    if value is None:
        value = _mapping_value(workstream, "acceptance")
    if value is None:
        value = _mapping_value(workstream, "covers")
    return _string_tuple(value) or _acceptance_ids(task)


def _workstream_sensors(
    workstream: Any,
    controls: Any,
    acceptance_ids: tuple[str, ...],
    global_sensors: tuple[str, ...],
) -> tuple[str, ...]:
    direct = _string_tuple(_mapping_value(workstream, "sensors"))
    if direct:
        return direct

    covered = []
    for sensor in _control_sensors(controls):
        covers = _string_tuple(_value(sensor, "covers"))
        if not covers or any(acceptance_id in covers for acceptance_id in acceptance_ids):
            sensor_id = _value(sensor, "id")
            if isinstance(sensor_id, str) and sensor_id and sensor_id not in covered:
                covered.append(sensor_id)
    return tuple(covered) or global_sensors


def _control_sensors(controls: Any) -> tuple[Any, ...]:
    value = _value(controls, "sensors") if controls is not None else None
    return tuple(value) if isinstance(value, list | tuple) else ()


def _global_control_sensor_ids(controls: Any) -> tuple[str, ...]:
    ids: list[str] = []
    for sensor in _control_sensors(controls):
        if _string_tuple(_value(sensor, "covers")):
            continue
        sensor_id = _value(sensor, "id")
        if isinstance(sensor_id, str) and sensor_id and sensor_id not in ids:
            ids.append(sensor_id)
    return tuple(ids)


def _has_global_read_only_controls(controls: Any) -> bool:
    for sensor in _control_sensors(controls):
        config = _value(sensor, "config")
        if isinstance(config, dict) and config.get("read_only") is True and not _string_tuple(_value(sensor, "covers")):
            return True
    return False


def _read_only_sensors_only(sensor_ids: tuple[str, ...], controls: Any) -> bool:
    if not sensor_ids:
        return False
    sensors_by_id = {
        _value(sensor, "id"): sensor
        for sensor in _control_sensors(controls)
        if isinstance(_value(sensor, "id"), str)
    }
    for sensor_id in sensor_ids:
        sensor = sensors_by_id.get(sensor_id)
        config = _value(sensor, "config") if sensor is not None else None
        if isinstance(config, dict) and config.get("read_only") is True:
            continue
        if sensor_id not in _READ_ONLY_SENSOR_IDS:
            return False
    return True


def _is_read_only_task(task: Any) -> bool:
    intent = _value(task, "intent")
    return _mapping_value(intent, "read_only") is True or _contains_read_only(_value(task, "constraints"))


def _contains_read_only(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("read_only") is True or any(_contains_read_only(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_read_only(item) for item in value)
    return False


def _is_blocked(workstream: Any) -> bool:
    if _mapping_value(workstream, "blocked") is True:
        return True
    status = _mapping_value(workstream, "status")
    return isinstance(status, str) and status.lower() == "blocked"


def _acceptance_ids(task: Any) -> tuple[str, ...]:
    direct = _value(task, "acceptance_ids")
    if direct is not None:
        return _string_tuple(direct)
    ids: list[str] = []
    for item in _value(task, "acceptance_matrix") or ():
        value = _mapping_value(item, "id")
        if isinstance(value, str) and value:
            ids.append(value)
    return tuple(ids)


def _task_id(task: Any) -> str:
    value = _value(task, "id")
    return value if isinstance(value, str) and value else "task"


def _workstream_id(workstream: Any, index: int) -> str:
    value = _mapping_value(workstream, "id")
    return value if isinstance(value, str) and value else f"workstream-{index + 1}"


def _assignment_id(task_id: str, workstream_id: str | None, role: str) -> str:
    parts = [_slug(task_id)]
    if workstream_id:
        parts.append(_slug(workstream_id))
    parts.append(_slug(role))
    return "assign-" + "-".join(part for part in parts if part)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _assignee(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list | tuple):
        names = [item for item in value if isinstance(item, str) and item]
        return ",".join(names) if names else None
    if isinstance(value, dict):
        for key in ("assignee", "owner", "id", "name"):
            item = value.get(key)
            if isinstance(item, str) and item:
                return item
    return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        raw = value.get("raw")
        if isinstance(raw, dict):
            return raw.get(key)
        return None
    direct = getattr(value, key, None)
    if direct is not None:
        return direct
    raw = getattr(value, "raw", None)
    if isinstance(raw, dict):
        return raw.get(key)
    return None
