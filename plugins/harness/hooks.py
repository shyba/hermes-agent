"""Plugin hook implementations for active harness runs."""

from __future__ import annotations

import re
import shlex
from typing import Any

from . import context, continuation, events, paths, prompt_context
from . import finalization
from .report import render_final_report
from .runner import load_active_task_for_run
from .task import HarnessTask


_READ_TOOL_HINTS = (
    "read",
    "search",
    "list",
    "find",
    "grep",
    "rg",
    "cat",
    "open",
    "view",
    "status",
    "show",
    "log",
)
_VERIFY_STARTS = (
    "scripts/run_tests.sh",
    "pytest",
    "python3 -m pytest",
    "cargo test",
    "cargo check",
    "npm test",
    "npm run build",
    "npm run type-check",
    "npm run lint",
    "git status",
    "git diff",
)
_MUTATING_COMMAND_PATTERNS = (
    r"(^|\s)touch\s+",
    r"(^|\s)rm\s+",
    r"(^|\s)mv\s+",
    r"(^|\s)cp\s+",
    r"(^|\s)mkdir\s+",
    r"(^|\s)rmdir\s+",
    r"(^|\s)sed\s+.*\s-i(\s|$)",
    r"(^|\s)perl\s+.*\s-pi(\s|$)",
    r"(^|\s)git\s+(add|commit|push|reset|checkout|stash|rebase|merge|clean|apply)\b",
    r"(^|\s)npm\s+(install|i|ci|update|uninstall)\b",
    r"(^|\s)(pip|pip3)\s+(install|uninstall)\b",
    r"(^|\s)python3\s+-m\s+pip\s+(install|uninstall)\b",
)
_MUTATING_NAME_HINTS = ("patch", "write", "edit", "delete", "move")


def register_hooks(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("transform_final_response", transform_final_response)
    ctx.register_hook("on_session_end", on_session_end)


def pre_llm_call(**kw: Any) -> dict[str, str] | None:
    active_context = prompt_context.build_active_run_context()
    if not active_context:
        return None
    return {"context": active_context}


def pre_tool_call(
    tool_name: str,
    args: dict[str, Any] | None,
    task_id: str | None = None,
    session_id: str | None = None,
    tool_call_id: str | None = None,
    **kw: Any,
) -> dict[str, str] | None:
    active = _active_run()
    if active is None:
        return None

    run_id, loaded_task, log = active
    fx = classify_tool(tool_name, args)
    if _is_read_only_task(loaded_task) and fx == "mutate":
        message = f"harness read-only policy blocked mutating tool: {tool_name}"
        log.append(
            "policy",
            {
                "event": "blocked",
                "policy": "read_only",
                "action": "violation",
                "run": run_id,
                "tool": tool_name,
                "fx": fx,
                "reason": "read_only",
                "args": args,
                "task_id": task_id,
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "ok": False,
            },
        )
        return {"action": "block", "message": message}

    if fx in {"read", "verify"}:
        log.append(
            "tool",
            {
                "event": "started",
                "run": run_id,
                "tool": tool_name,
                "fx": fx,
                "task_id": task_id,
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "ok": True,
            },
        )
    return None


def post_tool_call(
    tool_name: str,
    args: dict[str, Any] | None,
    result: Any,
    task_id: str | None = None,
    session_id: str | None = None,
    tool_call_id: str | None = None,
    duration_ms: int | float | None = None,
    **kw: Any,
) -> None:
    active = _active_run(load_contract=False)
    if active is None:
        return

    run_id, _loaded_task, log = active
    log.append(
        "tool",
        {
            "event": "finished",
            "run": run_id,
            "tool": tool_name,
            "fx": classify_tool(tool_name, args),
            "ok": _result_ok(result),
            "duration_ms": duration_ms,
            "task_id": task_id,
            "session_id": session_id,
            "tool_call_id": tool_call_id,
        },
    )


def transform_tool_result(
    tool_name: str,
    args: dict[str, Any] | None,
    result: Any,
    task_id: str | None = None,
    session_id: str | None = None,
    tool_call_id: str | None = None,
    duration_ms: int | float | None = None,
    **kw: Any,
) -> str | None:
    if not isinstance(result, str):
        return None
    active = _active_run(load_contract=False)
    if active is None:
        return None

    run_id, _loaded_task, log = active
    replacement, metadata = context.tombstone_tool_result(tool_name, args, result)
    if not metadata.get("tombstoned"):
        return None

    log.append(
        "context",
        {
            "event": "tool_result_tombstoned",
            "run": run_id,
            "tool": tool_name,
            "fx": classify_tool(tool_name, args),
            "artifact_path": metadata.get("artifact_path"),
            "original_chars": metadata.get("original_chars"),
            "preview_chars": metadata.get("preview_chars"),
            "duration_ms": duration_ms,
            "task_id": task_id,
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "ok": True,
        },
    )
    return replacement


def transform_final_response(
    session_id: str | None,
    task_id: str | None,
    final_response: str,
    completed: bool,
    interrupted: bool,
    conversation_history: list[dict[str, Any]] | None,
    model: str | None,
    platform: str | None,
    **kw: Any,
) -> dict[str, Any] | None:
    if interrupted:
        return None

    active = _active_run(load_contract=True)
    if active is None:
        return None

    run_id, loaded_task, log = active
    if loaded_task is None:
        return None

    try:
        loaded_events = log.read_all()
    except Exception:
        return None
    if not loaded_events:
        return None

    marker_reason = continuation.extract_dead_end_marker(final_response)
    if (
        marker_reason
        and not continuation.is_recoverable_dead_end_reason(marker_reason)
        and continuation.latest_dead_end(loaded_events) is None
    ):
        try:
            continuation.append_dead_end_marker_event(
                log,
                run_id=run_id,
                reason=marker_reason,
                session_id=session_id,
                task_id=task_id,
            )
            loaded_events = log.read_all()
        except Exception:
            pass

    result = finalization.evaluate(loaded_task, loaded_events)
    dead_end = continuation.latest_dead_end(loaded_events) if result.status != "complete" else None
    report = render_final_report(loaded_task, loaded_events, result, final_response)
    if dead_end is not None:
        reason = continuation.describe_dead_end(dead_end)
        if reason:
            report = f"{report}\nDead end: {reason}"
    if _should_append_finalization_event(loaded_events, result):
        log.append(
            "finalization",
            {
                "event": "transform_final_response",
                "run": run_id,
                "status": result.status,
                "complete_ids": list(result.complete_ids),
                "missing_ids": list(result.missing_ids),
                "blocking": list(result.blocking),
                "session_id": session_id,
                "task_id": task_id,
                "completed": bool(completed),
                "model": model,
                "platform": platform,
                "dead_end": dead_end is not None,
                "ok": result.status == "complete",
            },
        )

    transformed: dict[str, Any] = {
        "final_response": report,
        "metadata": {
            "harness": {
                "run_id": run_id,
                "status": result.status,
                "complete_ids": list(result.complete_ids),
                "missing_ids": list(result.missing_ids),
                "blocking": list(result.blocking),
                "dead_end": dead_end is not None,
            },
        },
    }
    if dead_end is not None:
        transformed["metadata"]["harness"]["dead_end_reason"] = continuation.describe_dead_end(dead_end)
    elif result.status != "complete":
        transformed["completed"] = False
        transformed["partial"] = True
        transformed["pending_steer"] = continuation.build_continuation_prompt(run_id, result)
    return transformed


def on_session_end(
    session_id: str | None,
    completed: bool,
    interrupted: bool,
    **kw: Any,
) -> None:
    active = _active_run(load_contract=False)
    if active is None:
        return

    run_id, _loaded_task, log = active
    log.append(
        "session",
        {
            "event": "ended",
            "run": run_id,
            "session_id": session_id,
            "completed": bool(completed),
            "interrupted": bool(interrupted),
            "ok": bool(completed) and not bool(interrupted),
        },
    )


def classify_tool(tool_name: str, args: dict[str, Any] | None = None) -> str:
    name = (tool_name or "").lower()
    command = _command_text(args)
    if "terminal" in name:
        return _classify_terminal(command)
    if any(hint in name for hint in _MUTATING_NAME_HINTS):
        return "mutate"
    if any(hint in name for hint in _READ_TOOL_HINTS):
        return "read"
    return "unknown"


def _classify_terminal(command: str) -> str:
    normalized = " ".join(command.strip().split())
    if not normalized:
        return "unknown"
    for pattern in _MUTATING_COMMAND_PATTERNS:
        if re.search(pattern, normalized):
            return "mutate"
    for prefix in _VERIFY_STARTS:
        if normalized == prefix or normalized.startswith(f"{prefix} "):
            return "verify"
    try:
        words = shlex.split(normalized)
    except ValueError:
        words = normalized.split()
    if not words:
        return "unknown"
    if words[0] in {"cat", "head", "tail", "sed", "awk", "rg", "grep", "ls", "find", "pwd", "wc"}:
        return "read"
    return "unknown"


def _active_run(load_contract: bool = True) -> tuple[str, HarnessTask | None, events.EventLog] | None:
    try:
        run_id = paths.get_active_run()
    except Exception:
        return None
    if run_id is None:
        return None

    loaded_task = None
    if load_contract:
        try:
            loaded_task, _task_error, _task_source = load_active_task_for_run(run_id)
        except Exception:
            loaded_task = None
    try:
        log = events.EventLog.for_run(run_id)
    except Exception:
        return None
    return run_id, loaded_task, log


def _should_append_finalization_event(
    loaded_events: list[events.HarnessEvent],
    result: finalization.FinalizationResult,
) -> bool:
    if not loaded_events:
        return False
    last = loaded_events[-1]
    if last.t != "finalization":
        return True
    payload = last.payload
    return (
        payload.get("status") != result.status
        or tuple(payload.get("complete_ids", ())) != result.complete_ids
        or tuple(payload.get("missing_ids", ())) != result.missing_ids
        or tuple(payload.get("blocking", ())) != result.blocking
    )


def _is_read_only_task(loaded_task: HarnessTask | None) -> bool:
    if loaded_task is None:
        return False
    if loaded_task.intent.get("read_only") is True:
        return True
    return _contains_read_only_constraint(loaded_task.constraints)


def _contains_read_only_constraint(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("read_only") is True:
            return True
        return any(_contains_read_only_constraint(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_read_only_constraint(item) for item in value)
    return False


def _command_text(args: dict[str, Any] | None) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd", "shell_command"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        ok = result.get("ok")
        if isinstance(ok, bool):
            return ok
        success = result.get("success")
        if isinstance(success, bool):
            return success
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code == 0
    if isinstance(result, str):
        lowered = result.lower()
        return not any(marker in lowered for marker in ("\"success\": false", "\"ok\": false", "\"exit_code\": 1"))
    return result is not None
