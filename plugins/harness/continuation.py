"""Continuation helpers for active harness runs."""

from __future__ import annotations

import json
import re
from typing import Any

from . import events, paths
from .validation import CHECK_TOOL_NAME


DEAD_END_TOOL_NAME = "harness_report_dead_end"
RESOLVE_TOOL_NAME = "harness_resolve_blockers"
DEAD_END_MARKER = "HARNESS_DEAD_END:"
_RECOVERABLE_DEAD_END_MARKERS = (
    "stale",
    "historical",
    "history",
    "orphan",
    "orphaned",
    "tombstone",
    "tombstoned",
    "failed tool event",
    "failed tool call",
    "generic.no_unresolved_failed_tools",
    "prior context",
    "previous context",
    "compacted",
    "old ledger",
    "prior session",
    "previous session",
    "cannot be cleared",
    "cannot be overwritten",
    "cannot be superseded",
    "superseded",
)


DEAD_END_SCHEMA = {
    "name": DEAD_END_TOOL_NAME,
    "description": (
        "Record that an active harness run has reached a real dead end. "
        "Use only after trying reasonable recovery and determining the "
        "remaining blockers cannot be solved in this environment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Concrete reason the blocker cannot be solved.",
            },
            "blocker_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Harness blocking IDs or missing acceptance IDs involved.",
            },
            "attempts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Brief list of recovery attempts already made.",
            },
        },
        "required": ["reason"],
    },
}

RESOLVE_SCHEMA = {
    "name": RESOLVE_TOOL_NAME,
    "description": (
        "Mark active harness blocker events as resolved after fresh recovery or "
        "validation. Use this for stale, orphaned, retried, or superseded failed "
        "tool/sensor/delegate events instead of reporting a dead end."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "blocker_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Blocking event IDs or sequence numbers, such as '61', 'ev61', or '115-1083'. Use ['all'] to resolve every currently unresolved failed tool/sensor/delegate/policy event.",
            },
            "resolve_all": {
                "type": "boolean",
                "description": "Resolve every currently unresolved failed tool/sensor/delegate/policy event.",
            },
            "reason": {
                "type": "string",
                "description": "Why the blockers are now resolved.",
            },
            "evidence": {
                "type": "string",
                "description": "Fresh validation or investigation evidence supporting the resolution.",
            },
        },
        "required": ["reason"],
    },
}


def register_dead_end_tool(ctx: Any) -> None:
    ctx.register_tool(
        name=DEAD_END_TOOL_NAME,
        toolset="harness",
        schema=DEAD_END_SCHEMA,
        handler=handle_report_dead_end,
        description="Record an unrecoverable harness blocker.",
        emoji="!",
    )
    ctx.register_tool(
        name=RESOLVE_TOOL_NAME,
        toolset="harness",
        schema=RESOLVE_SCHEMA,
        handler=handle_resolve_blockers,
        description="Resolve recoverable harness blocker events.",
        emoji=">",
    )


def handle_report_dead_end(args: dict[str, Any] | None = None, **kw: Any) -> str:
    """Tool handler used by the model to stop harness auto-continuation."""

    payload = args if isinstance(args, dict) else {}
    reason = _clean_text(payload.get("reason"))
    if not reason:
        return json.dumps(
            {
                "success": False,
                "error": "reason is required",
            }
        )
    if is_recoverable_dead_end_reason(reason):
        return json.dumps(
            {
                "success": True,
                "accepted": False,
                "recoverable": True,
                "error": (
                    "historical/tombstoned/stale ledger blockers are recoverable; "
                    f"use {RESOLVE_TOOL_NAME} with resolve_all=true after fresh validation instead"
                ),
            }
        )

    try:
        run_id = paths.get_active_run()
    except Exception as exc:
        return json.dumps({"success": False, "error": f"could not read active harness run: {exc}"})
    if run_id is None:
        return json.dumps({"success": False, "error": "no active harness run"})

    try:
        log = events.EventLog.for_run(run_id)
        event = log.append(
            "dead_end",
            {
                "event": "reported",
                "run": run_id,
                "reason": reason,
                "blocker_ids": _clean_strings(payload.get("blocker_ids")),
                "attempts": _clean_strings(payload.get("attempts")),
                "ok": True,
            },
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": f"could not record dead end: {exc}"})

    return json.dumps(
        {
            "success": True,
            "run_id": run_id,
            "event_sequence": event.s,
            "message": "dead end recorded; harness auto-continuation will stop",
        }
    )


def handle_resolve_blockers(args: dict[str, Any] | None = None, **kw: Any) -> str:
    """Tool handler used by the model to resolve recoverable blocker events."""

    payload = args if isinstance(args, dict) else {}
    reason = _clean_text(payload.get("reason"))
    if not reason:
        return json.dumps({"success": False, "error": "reason is required"})

    try:
        run_id = paths.get_active_run()
    except Exception as exc:
        return json.dumps({"success": False, "error": f"could not read active harness run: {exc}"})
    if run_id is None:
        return json.dumps({"success": False, "error": "no active harness run"})

    try:
        log = events.EventLog.for_run(run_id)
        loaded_events = log.read_all()
        resolve_all = bool(payload.get("resolve_all")) or _includes_all(payload.get("blocker_ids"))
        blocker_ids = (
            _unresolved_failed_event_sequences(loaded_events)
            if resolve_all
            else _event_sequences(payload.get("blocker_ids"))
        )
        if not blocker_ids:
            if resolve_all:
                return json.dumps(
                    {
                        "success": True,
                        "run_id": run_id,
                        "resolves": [],
                        "message": "no currently unresolved failed blocker events matched",
                    }
                )
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        "no blocker events matched; pass blocker_ids such as ['61'], "
                        "['115-1083'], or use resolve_all=true"
                    ),
                }
            )
        event = log.append(
            "resolution",
            {
                "event": "resolved_blockers",
                "run": run_id,
                "resolves": blocker_ids,
                "reason": reason,
                "evidence": _clean_text(payload.get("evidence")),
                "ok": True,
            },
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": f"could not record blocker resolution: {exc}"})

    return json.dumps(
        {
            "success": True,
            "run_id": run_id,
            "event_sequence": event.s,
            "resolves": blocker_ids,
            "message": "blockers marked resolved; continue and run harness checks again",
        }
    )


def latest_dead_end(loaded_events: list[Any]) -> Any | None:
    for event in reversed(loaded_events):
        if _event_type(event) != "dead_end":
            continue
        if _payload_value(event, "ok", None) is not True:
            continue
        reason = _payload_value(event, "reason", "")
        if isinstance(reason, str) and reason.strip():
            if is_recoverable_dead_end_reason(reason):
                continue
            return event
    return None


def is_recoverable_dead_end_reason(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _RECOVERABLE_DEAD_END_MARKERS)


def extract_dead_end_marker(response: str) -> str | None:
    """Read a last-resort dead-end marker from final prose.

    The structured tool is preferred. The marker lets restricted toolsets still
    terminate an otherwise endless continuation loop.
    """

    if not isinstance(response, str):
        return None
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith(DEAD_END_MARKER):
            reason = stripped[len(DEAD_END_MARKER):].strip()
            return reason or None
    return None


def append_dead_end_marker_event(
    log: events.EventLog,
    *,
    run_id: str,
    reason: str,
    session_id: str | None,
    task_id: str | None,
) -> events.HarnessEvent:
    return log.append(
        "dead_end",
        {
            "event": "reported",
            "run": run_id,
            "reason": reason,
            "source": "final_response_marker",
            "session_id": session_id,
            "task_id": task_id,
            "ok": True,
        },
    )


def build_continuation_prompt(run_id: str, result: Any) -> str:
    status = _string_attr(result, "status", "unverified")
    complete_ids = _format_ids(_string_tuple_attr(result, "complete_ids"))
    missing_ids = _format_ids(_string_tuple_attr(result, "missing_ids"))
    blocking_ids = _format_ids(_string_tuple_attr(result, "blocking"))
    message = _string_attr(result, "message", "")

    lines = [
        f"[Harness continuation for run {run_id}]",
        "The previous assistant response did not satisfy the active harness run.",
        f"Status: {status}",
        f"Complete acceptance IDs: {complete_ids}",
        f"Missing acceptance IDs: {missing_ids}",
        f"Blocking IDs: {blocking_ids}",
    ]
    if message:
        lines.append(f"Reason: {message}")
    lines.extend(
        [
            "",
            "Continue working now. Do not stop with a suggestion, plan, or claim of being blocked if a practical recovery exists.",
            f"Run `{CHECK_TOOL_NAME}` to append fresh validation evidence, then resolve recoverable blockers or create the missing verification.",
            f"If blockers are historical, tombstoned, stale, orphaned, retried, or superseded by fresh validation, call `{RESOLVE_TOOL_NAME}` with resolve_all=true or with blocker IDs/ranges and evidence, then continue.",
            f"If and only if the remaining blockers are impossible to solve in this environment, call `{DEAD_END_TOOL_NAME}` with a concrete reason, blocker_ids, and attempts.",
            f"If that tool is unavailable, end the response with `{DEAD_END_MARKER} <reason>`.",
        ]
    )
    return "\n".join(lines)


def describe_dead_end(event: Any) -> str:
    reason = _payload_value(event, "reason", "")
    return " ".join(reason.split()) if isinstance(reason, str) else ""


def _event_type(event: Any) -> str | None:
    value = getattr(event, "t", None)
    if isinstance(value, str):
        return value
    if isinstance(event, dict):
        value = event.get("t")
        return value if isinstance(value, str) else None
    return None


def _payload_value(event: Any, key: str, default: Any) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        return payload.get(key, default)
    return default


def _string_attr(value: Any, name: str, default: str) -> str:
    attr = getattr(value, name, default)
    return attr if isinstance(attr, str) and attr else default


def _string_tuple_attr(value: Any, name: str) -> tuple[str, ...]:
    attr = getattr(value, name, ())
    return tuple(item for item in _clean_strings(attr))


def _format_ids(ids: tuple[str, ...]) -> str:
    return ", ".join(ids) if ids else "none"


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _clean_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_clean_text(value)] if value.strip() else []
    try:
        items = list(value)
    except TypeError:
        return []
    cleaned = []
    for item in items:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def _event_sequences(value: Any) -> list[int]:
    sequences: list[int] = []
    for item in _clean_strings(value):
        for sequence in _parse_event_sequences(item):
            if sequence > 0 and sequence not in sequences:
                sequences.append(sequence)
    return sequences


def _includes_all(value: Any) -> bool:
    return any(item.lower() in {"all", "*", "current", "current_unresolved"} for item in _clean_strings(value))


def _unresolved_failed_event_sequences(loaded_events: list[Any]) -> list[int]:
    sequences: list[int] = []
    for event in loaded_events:
        event_type = _event_type(event)
        if event_type not in {"tool", "sensor", "delegate", "policy"}:
            continue
        sequence = _event_sequence(event)
        if sequence <= 0:
            continue
        failed = _payload_value(event, "ok", None) is False
        if event_type == "policy" and _payload_value(event, "action", None) == "violation":
            failed = True
        if not failed:
            continue
        if _is_resolved(sequence, loaded_events):
            continue
        sequences.append(sequence)
    return sequences


def _parse_event_sequences(text: str) -> list[int]:
    normalized = text.lower().replace(",", " ")
    found: list[int] = []
    for match in re.finditer(
        r"\b(?:ev|seq(?:uence)?s?)?\s*(\d+)\s*(?:-|through|to)\s*(?:ev|seq(?:uence)?s?)?\s*(\d+)\b",
        normalized,
    ):
        start = int(match.group(1))
        end = int(match.group(2))
        if start > end:
            start, end = end, start
        for sequence in range(start, end + 1):
            if sequence not in found:
                found.append(sequence)
    for match in re.finditer(r"\b(?:ev|seq(?:uence)?s?)?\s*(\d+)\b", normalized):
        sequence = int(match.group(1))
        if sequence not in found:
            found.append(sequence)
    return found


def _event_sequence(event: Any) -> int:
    value = getattr(event, "s", None)
    if isinstance(value, int):
        return value
    if isinstance(event, dict):
        value = event.get("s")
        if isinstance(value, int):
            return value
    return 0


def _is_resolved(failed_s: int, loaded_events: list[Any]) -> bool:
    for event in loaded_events:
        if _event_sequence(event) <= failed_s:
            continue
        if _matches_resolves(_payload_value(event, "resolves", None), failed_s):
            return True
    return False


def _matches_resolves(value: Any, failed_s: int) -> bool:
    if isinstance(value, int):
        return value == failed_s
    if isinstance(value, str):
        text = value.lower()
        if text.startswith("ev"):
            text = text[2:]
        return text == str(failed_s)
    if value is None or isinstance(value, (bytes, dict)):
        return False
    try:
        iterator = iter(value)
    except TypeError:
        return False
    return any(_matches_resolves(item, failed_s) for item in iterator)
