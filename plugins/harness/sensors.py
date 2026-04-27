"""Ledger sensors for Hermes harness runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class SensorResult:
    id: str
    ok: bool
    covers: tuple[str, ...] = field(default_factory=tuple)
    blocking: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""
    evidence_event: int | None = None


LEDGER_SENSOR_IDS: tuple[str, ...] = (
    "generic.verification_freshness",
    "generic.claims_have_evidence",
    "generic.no_unresolved_failed_tools",
    "generic.no_read_only_violation",
    "generic.no_done_after_timeout",
)

GIT_SENSOR_IDS: tuple[str, ...] = (
    "generic.git_status_clean",
)

_OUTPUT_PREVIEW_CHARS = 1200


def run_ledger_sensor(sensor_id: str, task: Any, events: list[Any]) -> SensorResult:
    if sensor_id == "generic.verification_freshness":
        return _run_coverage_sensor(sensor_id, task, events)
    if sensor_id == "generic.claims_have_evidence":
        return _run_coverage_sensor(sensor_id, task, events)
    if sensor_id == "generic.no_unresolved_failed_tools":
        return _run_no_unresolved_failed_tools(sensor_id, events)
    if sensor_id == "generic.no_read_only_violation":
        return _run_no_read_only_violation(sensor_id, events)
    if sensor_id == "generic.no_done_after_timeout":
        return _run_no_done_after_timeout(sensor_id, events)

    return SensorResult(
        id=sensor_id,
        ok=False,
        message=f"unknown ledger sensor id: {sensor_id}",
    )


def run_all_ledger_sensors(
    task: Any,
    events: list[Any],
    sensor_ids: list[str] | None = None,
) -> list[SensorResult]:
    ids = tuple(sensor_ids) if sensor_ids is not None else LEDGER_SENSOR_IDS
    return [run_ledger_sensor(sensor_id, task, events) for sensor_id in ids]


def run_command_sensor(
    sensor_spec: Any,
    repo_root: str | None = None,
    timeout_s: int = 120,
) -> SensorResult:
    sensor_id = _sensor_value(sensor_spec, "id")
    command = _sensor_value(sensor_spec, "command")
    covers = _string_tuple(_sensor_value(sensor_spec, "covers"))

    if not isinstance(sensor_id, str) or not sensor_id:
        return SensorResult(id="", ok=False, covers=covers, message="command sensor missing id")
    if not isinstance(command, str) or not command:
        return SensorResult(id=sensor_id, ok=False, covers=covers, message="command sensor missing command")

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        output = _combined_output(exc.stdout, exc.stderr)
        return SensorResult(
            id=sensor_id,
            ok=False,
            covers=covers,
            message=_command_message(command, "timeout", output, timeout_s=timeout_s),
        )
    except OSError as exc:
        return SensorResult(
            id=sensor_id,
            ok=False,
            covers=covers,
            message=f"command {command!r} failed to start: {exc}",
        )

    output = _combined_output(completed.stdout, completed.stderr)
    return SensorResult(
        id=sensor_id,
        ok=completed.returncode == 0,
        covers=covers,
        message=_command_message(command, completed.returncode, output),
    )


def run_git_sensor(sensor_id: str, repo_root: str | None = None) -> SensorResult:
    if sensor_id != "generic.git_status_clean":
        return SensorResult(id=sensor_id, ok=False, message=f"unknown git sensor id: {sensor_id}")

    try:
        completed = subprocess.run(
            "git status --short",
            shell=True,
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return SensorResult(id=sensor_id, ok=False, message="git status --short timed out")
    except OSError as exc:
        return SensorResult(id=sensor_id, ok=False, message=f"git unavailable: {exc}")

    output = _combined_output(completed.stdout, completed.stderr).strip()
    if completed.returncode != 0:
        return SensorResult(
            id=sensor_id,
            ok=False,
            message=_command_message("git status --short", completed.returncode, output),
        )

    return SensorResult(
        id=sensor_id,
        ok=not output,
        message=(
            "git status --short exit code 0; working tree clean"
            if not output
            else _command_message("git status --short", completed.returncode, output)
        ),
    )


def run_sensor(
    sensor_spec_or_id: Any,
    task: Any = None,
    events: list[Any] | None = None,
    repo_root: str | None = None,
) -> SensorResult:
    sensor_id = sensor_spec_or_id if isinstance(sensor_spec_or_id, str) else _sensor_value(sensor_spec_or_id, "id")
    if not isinstance(sensor_id, str) or not sensor_id:
        return SensorResult(id="", ok=False, message="sensor missing id")

    command = None if isinstance(sensor_spec_or_id, str) else _sensor_value(sensor_spec_or_id, "command")
    if isinstance(command, str) and command:
        return run_command_sensor(sensor_spec_or_id, repo_root=repo_root)

    if sensor_id in LEDGER_SENSOR_IDS:
        if task is not None and events is not None:
            return run_ledger_sensor(sensor_id, task, events)
        return SensorResult(id=sensor_id, ok=False, message=f"ledger sensor {sensor_id} requires task and events")

    if sensor_id in GIT_SENSOR_IDS:
        return run_git_sensor(sensor_id, repo_root=repo_root)

    return SensorResult(id=sensor_id, ok=False, message=f"unknown sensor id: {sensor_id}")


def _run_coverage_sensor(sensor_id: str, task: Any, events: list[Any]) -> SensorResult:
    acceptance_ids = _required_acceptance_ids(task)
    last_mutation_s = max(
        (
            event.s
            for event in events
            if event.t == "tool" and _payload(event).get("fx") == "mutate"
        ),
        default=0,
    )
    require_fresh = sensor_id == "generic.verification_freshness"

    covered: dict[str, int] = {}
    for event in events:
        payload = _payload(event)
        if event.t != "sensor" or payload.get("ok") is not True:
            continue
        if require_fresh and event.s <= last_mutation_s:
            continue
        for acceptance_id in _string_tuple(payload.get("covers")):
            if acceptance_id in acceptance_ids and _matches_required_evidence(task, acceptance_id, payload):
                covered[acceptance_id] = event.s

    covers = tuple(acceptance_id for acceptance_id in acceptance_ids if acceptance_id in covered)
    blocking = tuple(acceptance_id for acceptance_id in acceptance_ids if acceptance_id not in covered)
    ok = not blocking
    evidence_event = max(covered.values(), default=None)
    message = (
        "all acceptance ids have fresh sensor evidence"
        if ok
        else "missing fresh sensor evidence for acceptance ids: " + ", ".join(blocking)
    )
    return SensorResult(
        id=sensor_id,
        ok=ok,
        covers=covers,
        blocking=blocking,
        message=message,
        evidence_event=evidence_event,
    )


def _run_no_unresolved_failed_tools(sensor_id: str, events: list[Any]) -> SensorResult:
    failed = [
        event
        for event in events
        if event.t in {"tool", "sensor", "delegate"} and _payload(event).get("ok") is False
    ]
    blocking = tuple(str(event.s) for event in failed if not _is_resolved(event.s, events))
    return SensorResult(
        id=sensor_id,
        ok=not blocking,
        blocking=blocking,
        message=(
            "no unresolved failed tool, sensor, or delegate events"
            if not blocking
            else "unresolved failed event sequences: " + ", ".join(blocking)
        ),
    )


def _run_no_read_only_violation(sensor_id: str, events: list[Any]) -> SensorResult:
    violations = []
    for event in events:
        if event.t != "policy":
            continue
        payload = _payload(event)
        if payload.get("policy") == "read_only" and (
            payload.get("ok") is False or payload.get("action") == "violation"
        ):
            violations.append(event)

    blocking = tuple(str(event.s) for event in violations if not _is_resolved(event.s, events))
    return SensorResult(
        id=sensor_id,
        ok=not blocking,
        blocking=blocking,
        message=(
            "no read-only policy violations"
            if not blocking
            else "read-only policy violation events: " + ", ".join(blocking)
        ),
        evidence_event=violations[-1].s if violations else None,
    )


def _run_no_done_after_timeout(sensor_id: str, events: list[Any]) -> SensorResult:
    failures = []
    for event in events:
        if event.t != "delegate":
            continue
        payload = _payload(event)
        if payload.get("status") in {"timeout", "error"} and payload.get("required") is True:
            failures.append(event)

    blocking = tuple(str(event.s) for event in failures if not _is_resolved(event.s, events))
    return SensorResult(
        id=sensor_id,
        ok=not blocking,
        blocking=blocking,
        message=(
            "no unresolved required delegate timeout or error events"
            if not blocking
            else "unresolved required delegate timeout/error events: " + ", ".join(blocking)
        ),
    )


def _acceptance_ids(task: Any) -> tuple[str, ...]:
    ids = getattr(task, "acceptance_ids", None)
    if ids is None:
        matrix = getattr(task, "acceptance_matrix", None)
        if isinstance(matrix, dict):
            ids = matrix.keys()
    return _string_tuple(ids)


def _required_acceptance_ids(task: Any) -> tuple[str, ...]:
    policy = getattr(task, "finalization_policy", None)
    if isinstance(policy, dict):
        required = _string_tuple(policy.get("complete_requires"))
        if required:
            return required
    return _acceptance_ids(task)


def _matches_required_evidence(task: Any, acceptance_id: str, sensor_payload: dict[str, Any]) -> bool:
    required = _required_sensor_ids(task, acceptance_id)
    if not required:
        return True
    sensor_id = sensor_payload.get("id")
    return isinstance(sensor_id, str) and sensor_id in required


def _required_sensor_ids(task: Any, acceptance_id: str) -> tuple[str, ...]:
    matrix = getattr(task, "acceptance_matrix", ())
    for item in matrix:
        item_id = getattr(item, "id", None)
        if item_id != acceptance_id:
            continue
        evidence = getattr(item, "required_evidence", ())
        sensors: list[str] = []
        for entry in evidence:
            sensor = getattr(entry, "sensor", None)
            if isinstance(sensor, str) and sensor:
                sensors.append(sensor)
        return tuple(sensors)
    return ()


def _is_resolved(failed_s: int, events: list[Any]) -> bool:
    for event in events:
        if event.s <= failed_s:
            continue
        resolves = _payload(event).get("resolves")
        if _matches_resolves(resolves, failed_s):
            return True
    return False


def _matches_resolves(value: Any, failed_s: int) -> bool:
    if isinstance(value, int):
        return value == failed_s
    if isinstance(value, str):
        return value == str(failed_s)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict, str)):
        return any(_matches_resolves(item, failed_s) for item in value)
    return False


def _payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _sensor_value(sensor_spec: Any, key: str) -> Any:
    if isinstance(sensor_spec, dict):
        return sensor_spec.get(key)
    return getattr(sensor_spec, key, None)


def _combined_output(stdout: Any, stderr: Any) -> str:
    parts = []
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stdout, str) and stdout:
        parts.append(stdout)
    if isinstance(stderr, str) and stderr:
        parts.append(stderr)
    return "\n".join(part.rstrip("\n") for part in parts if part)


def _command_message(command: str, exit_code: int | str, output: str, timeout_s: int | None = None) -> str:
    status = f"command {command!r}"
    if exit_code == "timeout":
        status += f" timed out after {timeout_s}s"
    else:
        status += f" exited with code {exit_code}"

    preview = output.strip()
    if not preview:
        return status
    if len(preview) > _OUTPUT_PREVIEW_CHARS:
        preview = preview[:_OUTPUT_PREVIEW_CHARS].rstrip() + "\n...[truncated]"
    return f"{status}; output preview:\n{preview}"


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return tuple(item for item in value if isinstance(item, str))
    return ()
