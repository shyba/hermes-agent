"""Slash command surface for the bundled harness plugin."""

from __future__ import annotations

import shlex

from . import runner


_HELP_TEXT = """Harness commands:
/harness status
/harness run <openspec-change-id-or-path>
/harness check [sensor-id]"""


def handle_slash(raw_args: str = "") -> str:
    """Handle ``/harness`` slash command invocations."""
    try:
        argv = _parse_args(raw_args)
    except ValueError as exc:
        return f"Could not parse harness command: {exc}\n\n{_HELP_TEXT}"
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return _HELP_TEXT

    subcommand = argv[0].lower()
    if subcommand == "status":
        return _status()
    if subcommand == "run":
        return _run(" ".join(argv[1:]).strip())
    if subcommand == "check":
        return _check(argv[1] if len(argv) > 1 else None)
    return f"Unknown harness subcommand: {subcommand}\n\n{_HELP_TEXT}"


def _parse_args(raw_args: str) -> list[str]:
    return shlex.split(raw_args or "")


def _status() -> str:
    try:
        return runner.get_status()
    except Exception as exc:
        return f"Harness state error: {exc}"


def _run(task: str) -> str:
    if not task:
        return "Usage: /harness run <openspec-change-id-or-path>"

    try:
        result = runner.start_run(task)
    except Exception as exc:
        return f"Could not create harness run: {exc}"
    if isinstance(result, str):
        return result

    source_format = _result_value(result, "source_format", runner.SOURCE_FORMAT_LEGACY)
    if source_format == runner.SOURCE_FORMAT_OPENSPEC:
        return (
            f"Created harness run {_result_value(result, 'run_id')}.\n"
            f"Source: OpenSpec\n"
            f"OpenSpec change: {_result_value(result, 'source_path')}.\n"
            f"Path: {_result_value(result, 'run_path')}\n"
            f"Derived task snapshot: {_result_value(result, 'task_snapshot_path')}"
        )

    return (
        f"Created harness run {_result_value(result, 'run_id')}.\n"
        f"Task: {task}\n"
        "Source: legacy free-text task skeleton (deprecated)\n"
        f"Path: {_result_value(result, 'run_path')}\n"
        f"Task file: {_result_value(result, 'task_path')}"
    )


def _check(sensor_id: str | None = None) -> str:
    try:
        if sensor_id is None:
            return runner.run_check()
        return runner.run_check(sensor_id=sensor_id)
    except Exception as exc:
        return f"Harness check error: {exc}"


def _result_value(result: object, key: str, default: str = "") -> str:
    if isinstance(result, dict):
        return str(result.get(key, default))
    try:
        return str(result[key])  # type: ignore[index]
    except Exception:
        return default
