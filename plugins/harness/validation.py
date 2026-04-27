"""Validation tool surface for active harness runs."""

from __future__ import annotations

import json
from typing import Any

from . import runner


CHECK_TOOL_NAME = "harness_check"

CHECK_SCHEMA = {
    "name": CHECK_TOOL_NAME,
    "description": (
        "Run harness validation checks for the active run and append fresh "
        "sensor evidence. Use this before claiming harness completion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sensor_id": {
                "type": "string",
                "description": (
                    "Optional sensor id to run, such as "
                    "'generic.verification_freshness'. Omit to run configured "
                    "or default checks."
                ),
            },
        },
    },
}


def register_validation_tool(ctx: Any) -> None:
    ctx.register_tool(
        name=CHECK_TOOL_NAME,
        toolset="harness",
        schema=CHECK_SCHEMA,
        handler=handle_harness_check,
        description="Run harness validation checks for the active run.",
        emoji="?",
    )


def handle_harness_check(args: dict[str, Any] | None = None, **kw: Any) -> str:
    """Tool handler that runs validation without turning check failures into tool failures."""

    payload = args if isinstance(args, dict) else {}
    sensor_id = _clean_text(payload.get("sensor_id"))
    try:
        output = runner.run_check(sensor_id=sensor_id or None)
    except Exception as exc:
        output = f"Harness check error: {exc}"
        return _json_response(
            output=output,
            sensor_id=sensor_id,
            validation_status="error",
            validation_passed=False,
            error=str(exc),
        )

    status = _finalization_status(output)
    return _json_response(
        output=output,
        sensor_id=sensor_id,
        validation_status=status,
        validation_passed=status == "complete",
    )


def _json_response(
    *,
    output: str,
    sensor_id: str,
    validation_status: str,
    validation_passed: bool,
    error: str = "",
) -> str:
    payload: dict[str, Any] = {
        "success": True,
        "transport": "completed",
        "validation_status": validation_status,
        "validation_passed": validation_passed,
        "sensor_id": sensor_id,
        "output": output,
    }
    if error:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=True)


def _finalization_status(output: str) -> str:
    lowered = output.lower()
    if "no active harness run" in lowered:
        return "no_active_run"
    for line in output.splitlines():
        label, sep, value = line.partition(":")
        if sep and label.strip().lower() == "finalization":
            status = "_".join(value.strip().lower().split())
            return status or "unknown"
    if "harness check for " in lowered:
        return "checked"
    return "unknown"


def _clean_text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""
