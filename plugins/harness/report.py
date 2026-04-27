"""Final response report rendering for harness runs."""

from __future__ import annotations

from typing import Any


def render_final_report(
    task: Any,
    events: list[Any],
    finalization_result: Any,
    original_response: str,
) -> str:
    """Render a deterministic, evidence-backed final report."""

    status = _string_attr(finalization_result, "status", "unverified")
    complete_ids = _string_tuple_attr(finalization_result, "complete_ids")
    missing_ids = _string_tuple_attr(finalization_result, "missing_ids")
    blocking_ids = _string_tuple_attr(finalization_result, "blocking")
    summary = _task_summary(task)
    message = _string_attr(finalization_result, "message", "")
    original = _concise_original_response(original_response)

    lines = [
        "Harness final report",
        f"Status: {status}",
    ]
    if summary:
        lines.append(f"Task: {summary}")
    if message:
        lines.append(f"Reason: {message}")

    lines.extend(
        [
            f"Complete acceptance IDs: {_format_ids(complete_ids)}",
            f"Missing acceptance IDs: {_format_ids(missing_ids)}",
            f"Blocking IDs: {_format_ids(blocking_ids)}",
        ]
    )

    if status == "complete":
        lines.append("Conclusion: Required evidence is present; no extra completion is claimed beyond the listed IDs.")
    elif status == "blocked":
        lines.append("Conclusion: Blocked. Resolve the blocking IDs before treating this run as complete.")
    elif status == "partial":
        lines.append("Conclusion: Partial. Some evidence is present, but missing IDs remain.")
    else:
        lines.append("Conclusion: Unverified. Required fresh evidence is missing or sensors did not verify the run.")

    lines.append(f"Ledger events reviewed: {len(events)}")

    if original:
        lines.extend(["", "Original response", original])

    return "\n".join(lines).rstrip()


def _task_summary(task: Any) -> str:
    objective = getattr(task, "objective", None)
    if isinstance(objective, dict):
        summary = objective.get("summary")
        if isinstance(summary, str):
            return " ".join(summary.split())
    return ""


def _string_attr(value: Any, name: str, default: str) -> str:
    attr = getattr(value, name, default)
    return attr if isinstance(attr, str) and attr else default


def _string_tuple_attr(value: Any, name: str) -> tuple[str, ...]:
    attr = getattr(value, name, ())
    if isinstance(attr, str):
        return (attr,) if attr else ()
    if attr is None:
        return ()
    try:
        items = list(attr)
    except TypeError:
        return ()
    return tuple(item for item in items if isinstance(item, str) and item)


def _format_ids(ids: tuple[str, ...]) -> str:
    return ", ".join(ids) if ids else "none"


def _concise_original_response(response: str) -> str:
    if not isinstance(response, str):
        return ""
    normalized = "\n".join(line.rstrip() for line in response.strip().splitlines())
    if len(normalized) <= 800:
        return normalized
    return normalized[:797].rstrip() + "..."
