"""Prompt context for active harness runs."""

from __future__ import annotations

import json
from typing import Any

from . import events, finalization, paths
from .continuation import DEAD_END_MARKER, DEAD_END_TOOL_NAME, RESOLVE_TOOL_NAME
from .runner import load_active_task_for_run
from .validation import CHECK_TOOL_NAME


def build_active_run_context() -> str | None:
    """Return ephemeral user-message context for the active harness run."""

    try:
        run_id = paths.get_active_run()
    except Exception:
        return None
    if run_id is None:
        return None

    try:
        loaded_task, task_error, task_source = load_active_task_for_run(run_id)
    except Exception as exc:
        loaded_task, task_error, task_source = None, str(exc), ""
    try:
        loaded_events = events.EventLog.for_run(run_id).read_all()
    except Exception:
        loaded_events = []

    lines = [
        "<active_harness_run>",
        f"Run ID: {run_id}",
    ]
    if task_source:
        lines.append(f"Task source: {task_source}")

    if loaded_task is None:
        if task_error:
            lines.append(f"Task load error: {task_error}")
        lines.extend(_operating_rules())
        lines.append("</active_harness_run>")
        return "\n".join(lines)

    result = finalization.evaluate(loaded_task, loaded_events)
    summary = _string_mapping_value(loaded_task.objective, "summary")
    latest_request = _string_mapping_value(loaded_task.intent, "latest_user_request")
    if summary:
        lines.append(f"Task summary: {summary}")
    if latest_request and latest_request != summary:
        lines.append(f"Latest user request: {latest_request}")
    lines.append(f"Current finalization: {result.status}")
    if result.message:
        lines.append(f"Finalization reason: {result.message}")
    lines.append(f"Complete acceptance IDs: {_format_ids(result.complete_ids)}")
    lines.append(f"Missing acceptance IDs: {_format_ids(result.missing_ids)}")
    lines.append(f"Blocking IDs: {_format_ids(result.blocking)}")
    lines.append("Acceptance criteria:")
    for item in loaded_task.acceptance_matrix:
        lines.append(f"- {item.id}: {item.criterion}")
        evidence = _format_required_evidence(item.required_evidence)
        if evidence:
            lines.append(f"  required evidence: {evidence}")

    constraints = _concise_json(loaded_task.constraints)
    if constraints:
        lines.append(f"Constraints: {constraints}")
    workstreams = _concise_json(loaded_task.workstreams)
    if workstreams:
        lines.append(f"Workstreams: {workstreams}")

    lines.extend(_operating_rules())
    lines.append("</active_harness_run>")
    return "\n".join(lines)


def _operating_rules() -> list[str]:
    return [
        "Harness operating rules:",
        "- Treat the current user turn as instructions for this active harness run.",
        "- Do not switch to unrelated skills or tasks unless the active harness spec requires them.",
        "- Do not stop with only a plan, suggestion, or easy blocker; keep working until the harness finalization is complete.",
        f"- Validate with `{CHECK_TOOL_NAME}` before claiming completion. If operating from a shell, use `harness-check` or `hermes-harness check`.",
        f"- If blockers are historical, tombstoned, stale, orphaned, retried, or superseded, call `{RESOLVE_TOOL_NAME}` with resolve_all=true or blocker IDs/ranges plus fresh evidence, then continue.",
        f"- Never use `{DEAD_END_TOOL_NAME}` for generic.no_unresolved_failed_tools, failed tool tombstones, historical browser timeouts, or old failed sensor/tool/delegate rows.",
        "- A stop is valid only when finalization is complete or a true dead end has been recorded.",
        f"- For a true dead end, call `{DEAD_END_TOOL_NAME}` with reason, blocker_ids, and attempts.",
        f"- If that tool is unavailable, end the response with `{DEAD_END_MARKER} <reason>`.",
    ]


def _string_mapping_value(value: Any, key: str) -> str:
    if not isinstance(value, dict):
        return ""
    item = value.get(key)
    return " ".join(item.split()) if isinstance(item, str) else ""


def _format_required_evidence(items: Any) -> str:
    parts: list[str] = []
    try:
        iterable = list(items)
    except TypeError:
        return ""
    for item in iterable:
        sensor = getattr(item, "sensor", None)
        freshness = getattr(item, "freshness", None)
        if not isinstance(sensor, str) or not sensor:
            continue
        if isinstance(freshness, str) and freshness:
            parts.append(f"{sensor} ({freshness})")
        else:
            parts.append(sensor)
    return ", ".join(parts)


def _format_ids(ids: Any) -> str:
    try:
        items = [item for item in ids if isinstance(item, str) and item]
    except TypeError:
        items = []
    return ", ".join(items) if items else "none"


def _concise_json(value: Any, *, limit: int = 1200) -> str:
    if value in (None, "", [], {}):
        return ""
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        text = str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
