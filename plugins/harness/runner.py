"""Run management helpers for the bundled harness plugin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import controls, events, finalization, paths, sensors, task


TASK_FILENAME = "task.yaml"
TASK_SNAPSHOT_FILENAME = "task.snapshot.json"
NO_ACTIVE_RUN = "no active harness run"
SOURCE_FORMAT_LEGACY = "legacy-task-skeleton"
SOURCE_FORMAT_OPENSPEC = "openspec"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_path: str
    task_path: str = ""
    source_format: str = SOURCE_FORMAT_LEGACY
    source_path: str = ""
    task_snapshot_path: str = ""

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


def create_task_skeleton(run_id: str, user_request: str | None = None) -> dict[str, Any]:
    """Create a deterministic MVP task contract for a user request."""

    if user_request is None:
        user_request = run_id
        run_id = "manual-verification"
    request = user_request.strip()
    return {
        "version": "1",
        "id": run_id,
        "objective": {
            "summary": request,
        },
        "intent": {
            "latest_user_request": request,
        },
        "acceptance_matrix": [
            {
                "id": "manual-verification",
                "criterion": "The requested work has been manually verified by the operator.",
                "required_evidence": [],
            },
        ],
        "finalization_policy": {
            "complete_requires": ["manual-verification"],
        },
    }


def start_run(user_request: str, repo_root: str | os.PathLike[str] | None = None) -> RunResult:
    """Create a harness run from OpenSpec when possible, otherwise legacy free text."""

    request = user_request.strip()
    if not request:
        raise ValueError("user_request must be non-empty")

    openspec_path = resolve_openspec_change(request, repo_root)
    if openspec_path is not None:
        return start_openspec_run(openspec_path, repo_root=repo_root, requested=request)
    return start_legacy_run(request, repo_root=repo_root)


def start_legacy_run(user_request: str, repo_root: str | os.PathLike[str] | None = None) -> RunResult:
    """Create a legacy free-text harness run and persist a generated task.yaml."""

    request = user_request.strip()
    if not request:
        raise ValueError("user_request must be non-empty")

    run_id = paths.generate_unique_run_id(repo_root)
    run_path = paths.ensure_run_dir(run_id, repo_root)
    task_path = run_path / TASK_FILENAME
    skeleton = create_task_skeleton(run_id, request)

    task_path.write_text(json.dumps(skeleton, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.set_active_run(
        run_id,
        repo_root,
        metadata={
            "task": request,
            "task_path": str(task_path),
            "source_format": SOURCE_FORMAT_LEGACY,
        },
    )
    events.append_event(
        run_id,
        "run",
        {
            "event": "created",
            "run": run_id,
            "task": request,
            "task_path": str(task_path),
            "source_format": SOURCE_FORMAT_LEGACY,
            "ok": True,
        },
        repo_root,
    )
    events.append_event(
        run_id,
        "intent",
        {
            "event": "latest_user_request",
            "run": run_id,
            "latest_user_request": request,
            "ok": True,
        },
        repo_root,
    )

    return RunResult(
        run_id=run_id,
        run_path=str(run_path),
        task_path=str(task_path),
        source_format=SOURCE_FORMAT_LEGACY,
        source_path=str(task_path),
    )


def start_openspec_run(
    change_path: str | os.PathLike[str],
    repo_root: str | os.PathLike[str] | None = None,
    *,
    requested: str | None = None,
) -> RunResult:
    """Create a harness run from an existing OpenSpec change directory."""

    resolved_change_path = Path(change_path).resolve()
    loaded_change = _load_openspec_change(resolved_change_path)
    run_id = paths.generate_unique_run_id(repo_root)
    run_path = paths.ensure_run_dir(run_id, repo_root)
    snapshot = _openspec_change_to_task_snapshot(loaded_change, run_id, resolved_change_path, requested)
    snapshot_path = run_path / TASK_SNAPSHOT_FILENAME
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loaded_task = task.parse_task(snapshot)

    summary = loaded_task.objective.get("summary", resolved_change_path.name)
    change_id = _openspec_change_id(loaded_change, resolved_change_path)
    paths.set_active_run(
        run_id,
        repo_root,
        metadata={
            "task": summary,
            "source_format": SOURCE_FORMAT_OPENSPEC,
            "source_path": str(resolved_change_path),
            "change_id": change_id,
            "task_snapshot_path": str(snapshot_path),
            "snapshot_derived": True,
        },
    )
    events.append_event(
        run_id,
        "run",
        {
            "event": "created",
            "run": run_id,
            "task": summary,
            "source_format": SOURCE_FORMAT_OPENSPEC,
            "source_path": str(resolved_change_path),
            "change_id": change_id,
            "task_snapshot_path": str(snapshot_path),
            "snapshot_derived": True,
            "ok": True,
        },
        repo_root,
    )
    events.append_event(
        run_id,
        "intent",
        {
            "event": "latest_user_request",
            "run": run_id,
            "latest_user_request": summary,
            "ok": True,
        },
        repo_root,
    )

    return RunResult(
        run_id=run_id,
        run_path=str(run_path),
        source_format=SOURCE_FORMAT_OPENSPEC,
        source_path=str(resolved_change_path),
        task_snapshot_path=str(snapshot_path),
    )


def get_status(repo_root: str | os.PathLike[str] | None = None) -> str:
    """Return a concise status summary for the active harness run."""

    run_id = paths.get_active_run(repo_root)
    if run_id is None:
        return NO_ACTIVE_RUN

    run_path = paths.run_dir(run_id, repo_root, create=False)
    metadata = _active_metadata(repo_root)
    loaded_task, task_error, task_source = _load_active_task(run_id, run_path, metadata)
    loaded_events, events_error = _load_events(run_id, repo_root)

    lines = [f"Active harness run: {run_id}", f"Path: {run_path}"]
    source_format = metadata.get("source_format")
    if source_format == SOURCE_FORMAT_OPENSPEC:
        lines.append("Source: OpenSpec")
        source_path = metadata.get("source_path")
        if isinstance(source_path, str) and source_path:
            lines.append(f"OpenSpec change: {source_path}")
        snapshot_path = metadata.get("task_snapshot_path")
        if isinstance(snapshot_path, str) and snapshot_path:
            lines.append(f"Derived task snapshot: {snapshot_path}")
    elif source_format == SOURCE_FORMAT_LEGACY:
        lines.append("Source: legacy free-text task skeleton (deprecated)")
    if loaded_task is not None:
        summary = loaded_task.objective.get("summary")
        if isinstance(summary, str) and summary:
            lines.append(f"Task: {summary}")
    elif task_error is not None:
        lines.append(f"Task: unavailable from {task_source} ({task_error})")

    if loaded_task is not None and loaded_events is not None:
        result = finalization.evaluate(loaded_task, loaded_events)
        lines.append(f"Finalization: {result.status}")
        if result.message:
            lines.append(f"Reason: {result.message}")
    elif events_error is not None:
        lines.append(f"Events: unavailable ({events_error})")
    else:
        lines.append("Finalization: no events")

    return "\n".join(lines)


def run_check(
    repo_root: str | os.PathLike[str] | None = None,
    sensor_id: str | None = None,
) -> str:
    """Run ledger checks for the active harness run and append sensor evidence."""

    resolved_paths = paths.harness_paths(repo_root, create=False)
    resolved_repo_root = resolved_paths.repo_root
    run_id = paths.get_active_run(resolved_repo_root)
    if run_id is None:
        return "no active harness run; start one with /harness run <task>"

    run_path = paths.run_dir(run_id, resolved_repo_root, create=False)
    metadata = _active_metadata(resolved_repo_root)
    loaded_task, task_error, task_source = _load_active_task(run_id, run_path, metadata)
    if loaded_task is None:
        raise ValueError(f"could not load active harness task from {task_source}: {task_error}")
    loaded_events = events.EventLog.for_run(run_id, resolved_repo_root).read_all()
    loaded_controls = _load_controls(resolved_paths)
    selected_sensors = _selected_sensors(loaded_controls, sensor_id)

    if hasattr(sensors, "run_sensor"):
        results = [
            sensors.run_sensor(
                item,
                loaded_task,
                loaded_events,
                repo_root=str(resolved_repo_root),
            )
            for item in selected_sensors
        ]
    else:
        results = sensors.run_all_ledger_sensors(loaded_task, loaded_events, _sensor_ids(selected_sensors))

    log = events.EventLog.for_run(run_id, resolved_repo_root)
    for result in results:
        log.append("sensor", _sensor_payload(result))

    refreshed_events = log.read_all()
    final = finalization.evaluate(loaded_task, refreshed_events, _ledger_sensor_ids(loaded_controls))
    lines = [f"Harness check for {run_id}:"]
    for result in results:
        state = "ok" if getattr(result, "ok", False) is True else "failed"
        message = getattr(result, "message", "")
        line = f"- {getattr(result, 'id', '<unknown>')}: {state}"
        if message:
            line = f"{line} - {message}"
        lines.append(line)
    lines.append(f"Finalization: {final.status}")
    if final.message:
        lines.append(f"Reason: {final.message}")
    return "\n".join(lines)


def resolve_openspec_change(
    value: str,
    repo_root: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Resolve an existing OpenSpec change directory from a path or change id."""

    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()

    try:
        root = paths.find_repo_root(repo_root)
    except Exception:
        root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
    change_path = root / "openspec" / "changes" / value
    if change_path.is_dir():
        return change_path.resolve()
    return None


def run_openspec_validate(
    change_id: str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    timeout_s: int = 120,
) -> sensors.SensorResult:
    """Run ``openspec validate <change-id> --strict`` when the CLI is installed."""

    if shutil.which("openspec") is None:
        return sensors.SensorResult(
            id="openspec.validate.strict",
            ok=False,
            message="OpenSpec CLI is not available on PATH",
        )
    resolved_root = paths.find_repo_root(repo_root)
    try:
        completed = subprocess.run(
            ["openspec", "validate", change_id, "--strict"],
            cwd=resolved_root,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(str(part or "") for part in (exc.stdout, exc.stderr)).strip()
        return sensors.SensorResult(
            id="openspec.validate.strict",
            ok=False,
            message=f"openspec validate timed out after {timeout_s}s\n{output}".strip(),
        )
    except OSError as exc:
        return sensors.SensorResult(
            id="openspec.validate.strict",
            ok=False,
            message=f"openspec validate failed to start: {exc}",
        )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    message = f"openspec validate {change_id} --strict exited with code {completed.returncode}"
    if output:
        message = f"{message}\n{output[:1200]}"
    return sensors.SensorResult(
        id="openspec.validate.strict",
        ok=completed.returncode == 0,
        message=message,
    )


def load_active_task_for_run(
    run_id: str,
    repo_root: str | os.PathLike[str] | None = None,
) -> tuple[Any | None, str | None, str]:
    """Load a run task from the same active metadata source used by status/check."""

    run_path = paths.run_dir(run_id, repo_root, create=False)
    metadata = _active_metadata(repo_root)
    return _load_active_task(run_id, run_path, metadata)


def _load_task(task_path: Path) -> tuple[Any | None, str | None]:
    try:
        return task.load_task(task_path), None
    except Exception as exc:
        return None, str(exc)


def _load_active_task(
    run_id: str,
    run_path: Path,
    metadata: dict[str, Any],
) -> tuple[Any | None, str | None, str]:
    source_format = metadata.get("source_format")
    if source_format == SOURCE_FORMAT_OPENSPEC:
        source_path = metadata.get("source_path")
        if isinstance(source_path, str) and source_path:
            try:
                loaded_change = _load_openspec_change(Path(source_path))
                snapshot = _openspec_change_to_task_snapshot(
                    loaded_change,
                    run_id,
                    Path(source_path),
                    metadata.get("task") if isinstance(metadata.get("task"), str) else None,
                )
                return task.parse_task(snapshot), None, source_path
            except Exception as exc:
                source_error = str(exc)
        else:
            source_error = "missing source_path metadata"

        snapshot_path = Path(str(metadata.get("task_snapshot_path") or run_path / TASK_SNAPSHOT_FILENAME))
        try:
            return task.parse_task(json.loads(snapshot_path.read_text(encoding="utf-8"))), None, str(snapshot_path)
        except Exception as exc:
            return None, f"{source_error}; snapshot fallback failed: {exc}", str(snapshot_path)

    task_path = Path(str(metadata.get("task_path") or run_path / TASK_FILENAME))
    loaded_task, error = _load_task(task_path)
    return loaded_task, error, str(task_path)


def _active_metadata(repo_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    marker = paths.harness_paths(repo_root, create=False).active_run_file
    if not marker.exists():
        return {}
    data = json.loads(marker.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    return dict(metadata) if isinstance(metadata, dict) else {}


def _load_openspec_change(change_path: Path) -> Any:
    try:
        from .openspec import load_openspec_change
    except ImportError as exc:
        raise RuntimeError("plugins.harness.openspec.load_openspec_change is not available") from exc
    return load_openspec_change(change_path)


def _openspec_change_to_task_snapshot(
    loaded_change: Any,
    run_id: str,
    change_path: Path,
    requested: str | None,
) -> dict[str, Any]:
    if isinstance(loaded_change, task.HarnessTask):
        data = dict(loaded_change.raw or {})
    elif isinstance(loaded_change, dict):
        data = dict(loaded_change)
    else:
        raw = getattr(loaded_change, "raw", None)
        data = dict(raw) if isinstance(raw, dict) else {}

    if _looks_like_task(data):
        snapshot = dict(data)
    else:
        summary = _first_string(
            _mapping_value(data, "summary"),
            _mapping_value(data, "title"),
            _mapping_value(data, "description"),
            getattr(loaded_change, "summary", None),
            getattr(loaded_change, "title", None),
            requested,
            change_path.name,
        )
        snapshot = create_task_skeleton(run_id, summary)
        snapshot["id"] = _first_string(_mapping_value(data, "id"), getattr(loaded_change, "id", None), change_path.name)

    snapshot.setdefault("version", "1")
    snapshot.setdefault("id", change_path.name)
    snapshot.setdefault("objective", {"summary": change_path.name})
    snapshot.setdefault("intent", {"latest_user_request": snapshot["objective"].get("summary", change_path.name)})
    snapshot.setdefault(
        "acceptance_matrix",
        [
            {
                "id": "manual-verification",
                "criterion": "The OpenSpec change has been implemented and verified.",
                "required_evidence": [],
            }
        ],
    )
    snapshot.setdefault("finalization_policy", {"complete_requires": [snapshot["acceptance_matrix"][0]["id"]]})
    snapshot["_harness"] = {
        "source_format": SOURCE_FORMAT_OPENSPEC,
        "source_path": str(change_path),
        "snapshot_derived": True,
    }
    return snapshot


def _openspec_change_id(loaded_change: Any, change_path: Path) -> str:
    if isinstance(loaded_change, dict):
        value = loaded_change.get("id") or loaded_change.get("change_id")
    else:
        value = getattr(loaded_change, "id", None) or getattr(loaded_change, "change_id", None)
    return value if isinstance(value, str) and value else change_path.name


def _looks_like_task(data: dict[str, Any]) -> bool:
    return all(key in data for key in ("version", "id", "objective", "intent", "acceptance_matrix"))


def _mapping_value(data: dict[str, Any], key: str) -> Any:
    value = data.get(key)
    if isinstance(value, dict):
        return value.get("summary") or value.get("latest_user_request")
    return value


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "OpenSpec change"


def _load_events(
    run_id: str,
    repo_root: str | os.PathLike[str] | None,
) -> tuple[list[Any] | None, str | None]:
    try:
        path = events.events_path(run_id, repo_root, create=False)
        if not path.exists():
            return [], None
        return events.EventLog(path).read_all(), None
    except Exception as exc:
        return None, str(exc)


def _load_controls(resolved_paths: paths.HarnessPaths) -> controls.HarnessControls:
    controls_path = resolved_paths.harness_root / "controls.yaml"
    if controls_path.exists():
        return controls.load_controls(controls_path)
    return controls.default_controls()


def _selected_sensors(loaded_controls: controls.HarnessControls, sensor_id: str | None) -> list[Any]:
    if sensor_id:
        for sensor in loaded_controls.sensors:
            if sensor.id == sensor_id:
                return [sensor]
        return [sensor_id]
    return list(loaded_controls.sensors)


def _sensor_ids(items: list[Any]) -> list[str]:
    return [getattr(item, "id", item) for item in items if isinstance(getattr(item, "id", item), str)]


def _ledger_sensor_ids(loaded_controls: controls.HarnessControls) -> list[str]:
    return [
        sensor.id
        for sensor in loaded_controls.sensors
        if sensor.kind == "ledger" and not sensor.command
    ]


def _sensor_payload(result: Any) -> dict[str, Any]:
    payload = {
        "event": "check",
        "id": getattr(result, "id", "<unknown>"),
        "ok": bool(getattr(result, "ok", False)),
        "covers": list(getattr(result, "covers", ()) or ()),
        "blocking": list(getattr(result, "blocking", ()) or ()),
    }
    message = getattr(result, "message", "")
    if message:
        payload["message"] = message
    evidence_event = getattr(result, "evidence_event", None)
    if evidence_event is not None:
        payload["evidence_event"] = evidence_event
    return payload
