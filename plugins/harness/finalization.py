"""Deterministic finalization checks for Hermes harness tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_BLOCKING_EVENT_TYPES = frozenset({"tool", "delegate", "policy"})
_HARD_BLOCKING_SENSOR_IDS = frozenset({
    "generic.no_unresolved_failed_tools",
    "generic.no_read_only_violation",
    "generic.no_done_after_timeout",
})


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    complete_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    blocking: tuple[str, ...]
    sensor_results: tuple[Any, ...] = ()
    message: str = ""


def evaluate(
    task: Any,
    events: list[Any],
    sensor_ids: list[str] | None = None,
) -> FinalizationResult:
    """Evaluate harness completion from ledger events and required sensors."""

    acceptance_ids = _acceptance_ids(task)
    sensor_results = _run_ledger_sensors(task, events, sensor_ids)
    blocking = _blocking_reasons(events, sensor_results)

    last_mutation = _last_mutating_tool_sequence(events)
    complete_ids = _verified_acceptance_ids(acceptance_ids, events, last_mutation, task)
    complete_set = set(complete_ids)
    missing_ids = tuple(acceptance_id for acceptance_id in acceptance_ids if acceptance_id not in complete_set)

    if blocking:
        status = "blocked"
    elif not missing_ids and _all_sensors_passed(sensor_results):
        status = "complete"
    elif complete_ids and missing_ids and not blocking:
        status = "partial"
    elif blocking:
        status = "blocked"
    else:
        status = "unverified"

    return FinalizationResult(
        status=status,
        complete_ids=complete_ids,
        missing_ids=missing_ids,
        blocking=blocking,
        sensor_results=sensor_results,
        message=_message(status, complete_ids, missing_ids, blocking),
    )


def _acceptance_ids(task: Any) -> tuple[str, ...]:
    policy = getattr(task, "finalization_policy", None)
    if isinstance(policy, dict):
        required = policy.get("complete_requires")
        if required is not None:
            return _string_tuple(required)
    elif policy is not None:
        required = getattr(policy, "complete_requires", None)
        if required is not None:
            return _string_tuple(required)

    direct = getattr(task, "acceptance_ids", None)
    if direct is not None:
        return _string_tuple(direct)

    matrix = getattr(task, "acceptance_matrix", None)
    if matrix is None and isinstance(task, dict):
        matrix = task.get("acceptance_matrix")
    ids: list[str] = []
    if matrix is not None:
        for item in matrix:
            if isinstance(item, dict):
                value = item.get("id")
            else:
                value = getattr(item, "id", None)
            if isinstance(value, str) and value:
                ids.append(value)
    return tuple(ids)


def _run_ledger_sensors(task: Any, events: list[Any], sensor_ids: list[str] | None) -> tuple[Any, ...]:
    from .sensors import run_all_ledger_sensors

    result = run_all_ledger_sensors(task, events, sensor_ids)
    if result is None:
        return ()
    if isinstance(result, tuple):
        return result
    if isinstance(result, list):
        return tuple(result)
    return (result,)


def _verified_acceptance_ids(
    acceptance_ids: tuple[str, ...],
    events: list[Any],
    last_mutation: int,
    task: Any = None,
) -> tuple[str, ...]:
    accepted = set(acceptance_ids)
    verified: set[str] = set()
    for event in events:
        if _event_type(event) != "sensor" or _event_ok(event) is not True:
            continue
        if _event_sequence(event) <= last_mutation:
            continue
        sensor_after = _sensor_after(event)
        if sensor_after is not None and sensor_after < last_mutation:
            continue
        for covered_id in _event_covers(event):
            if covered_id in accepted and _matches_required_evidence(task, covered_id, event):
                verified.add(covered_id)
    return tuple(acceptance_id for acceptance_id in acceptance_ids if acceptance_id in verified)


def _last_mutating_tool_sequence(events: list[Any]) -> int:
    last = 0
    for event in events:
        if _event_type(event) != "tool":
            continue
        if not _is_mutating_tool_event(event):
            continue
        last = max(last, _event_sequence(event))
    return last


def _blocking_reasons(events: list[Any], sensor_results: tuple[Any, ...]) -> tuple[str, ...]:
    reasons: list[str] = []
    for event in events:
        if _event_type(event) not in _BLOCKING_EVENT_TYPES or _event_ok(event) is not False:
            continue
        if _is_resolved(_event_sequence(event), events):
            continue
        blocking = _payload_value(event, "blocking", True)
        if blocking is False:
            continue
        sequence = _event_sequence(event)
        if sequence > 0:
            reasons.append(str(sequence))

    for result in sensor_results:
        if _result_ok(result) is not False:
            continue
        if _result_id(result) not in _HARD_BLOCKING_SENSOR_IDS:
            continue
        for item in _result_blocking(result):
            if item not in reasons:
                reasons.append(item)
    return tuple(reasons)


def _all_sensors_passed(sensor_results: tuple[Any, ...]) -> bool:
    return all(_result_ok(result) is not False for result in sensor_results)


def _event_type(event: Any) -> str | None:
    value = getattr(event, "t", None)
    if isinstance(value, str):
        return value
    if isinstance(event, dict):
        value = event.get("t")
        return value if isinstance(value, str) else None
    return None


def _event_ok(event: Any) -> bool | None:
    value = getattr(event, "ok", None)
    if isinstance(value, bool):
        return value
    value = _payload_value(event, "ok", None)
    return value if isinstance(value, bool) else None


def _event_sequence(event: Any) -> int:
    value = getattr(event, "s", None)
    if isinstance(value, int):
        return value
    if isinstance(event, dict):
        value = event.get("s")
        if isinstance(value, int):
            return value
    return 0


def _event_covers(event: Any) -> tuple[str, ...]:
    return _string_tuple(_payload_value(event, "covers", ()))


def _sensor_after(event: Any) -> int | None:
    value = _payload_value(event, "after", None)
    return value if isinstance(value, int) else None


def _payload_string(event: Any, key: str) -> str | None:
    value = _payload_value(event, key, None)
    return value if isinstance(value, str) and value else None


def _payload_value(event: Any, key: str, default: Any) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        return payload.get(key, default)
    return default


def _result_ok(result: Any) -> bool | None:
    if isinstance(result, bool):
        return result
    value = getattr(result, "ok", None)
    if isinstance(value, bool):
        return value
    if isinstance(result, dict):
        value = result.get("ok")
        return value if isinstance(value, bool) else None
    return None


def _result_id(result: Any) -> str | None:
    value = getattr(result, "id", None)
    if isinstance(value, str):
        return value
    if isinstance(result, dict):
        value = result.get("id")
        return value if isinstance(value, str) else None
    return None


def _result_blocking(result: Any) -> tuple[str, ...]:
    if isinstance(result, dict):
        value = result.get("blocking")
    else:
        value = getattr(result, "blocking", ())
    return _string_tuple(value)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if value is None:
        return ()
    try:
        items = list(value)
    except TypeError:
        return ()
    return tuple(item for item in items if isinstance(item, str) and item)


def _matches_required_evidence(task: Any, acceptance_id: str, event: Any) -> bool:
    required = _required_sensor_ids(task, acceptance_id)
    if not required:
        return True
    sensor_id = _payload_value(event, "id", None)
    return isinstance(sensor_id, str) and sensor_id in required


def _required_sensor_ids(task: Any, acceptance_id: str) -> tuple[str, ...]:
    matrix = getattr(task, "acceptance_matrix", ())
    for item in matrix:
        if getattr(item, "id", None) != acceptance_id:
            continue
        sensors: list[str] = []
        for evidence in getattr(item, "required_evidence", ()):
            sensor = getattr(evidence, "sensor", None)
            if isinstance(sensor, str) and sensor:
                sensors.append(sensor)
        return tuple(sensors)
    return ()


def _is_mutating_tool_event(event: Any) -> bool:
    fx = _payload_value(event, "fx", None)
    if fx is not None:
        return fx == "mutate"
    mutating = _payload_value(event, "mutating", _payload_value(event, "mutates", None))
    return mutating is True


def _is_resolved(failed_s: int, events: list[Any]) -> bool:
    for event in events:
        if _event_sequence(event) <= failed_s:
            continue
        if _matches_resolves(_payload_value(event, "resolves", None), failed_s):
            return True
    return False


def _matches_resolves(value: Any, failed_s: int) -> bool:
    if isinstance(value, int):
        return value == failed_s
    if isinstance(value, str):
        return value == str(failed_s)
    if value is None or isinstance(value, (bytes, dict)):
        return False
    try:
        iterator = iter(value)
    except TypeError:
        return False
    return any(_matches_resolves(item, failed_s) for item in iterator)


def _message(
    status: str,
    complete_ids: tuple[str, ...],
    missing_ids: tuple[str, ...],
    blocking: tuple[str, ...],
) -> str:
    if status == "complete":
        return "all required acceptance evidence is fresh and ledger sensors passed"
    if status == "blocked":
        return "blocked by " + ", ".join(blocking) if blocking else "blocked by required ledger sensor failure"
    if status == "partial":
        return "fresh evidence exists for some acceptance ids; missing " + ", ".join(missing_ids)
    if missing_ids:
        return "missing fresh evidence for " + ", ".join(missing_ids)
    if complete_ids:
        return "acceptance evidence is present but ledger sensors are not verified"
    return "no fresh acceptance evidence found"
