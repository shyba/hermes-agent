"""Contract tests for harness final report rendering."""

from __future__ import annotations

from pathlib import Path

from plugins.harness.events import EventLog
from plugins.harness.finalization import evaluate
from plugins.harness.report import render_final_report
from plugins.harness.task import load_task


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness"


def _task():
    return load_task(FIXTURE_ROOT / "task-complete.yaml")


def _report(tmp_path: Path, original: str = "Original concise response.") -> str:
    log = EventLog(tmp_path / "events.jsonl")
    log.append("sensor", {"id": "tests.focused", "covers": ["ac1", "ac2"], "ok": True})
    rows = log.read_all()
    result = evaluate(_task(), rows)
    return render_final_report(_task(), rows, result, original)


def test_render_final_report_complete_does_not_overstate(tmp_path: Path):
    report = _report(tmp_path, "Implemented the requested changes.")

    assert "Status: complete" in report
    assert "Complete acceptance IDs: ac1, ac2" in report
    assert "Missing acceptance IDs: none" in report
    assert "Blocking IDs: none" in report
    assert "no extra completion is claimed beyond the listed IDs" in report
    assert "Original response\nImplemented the requested changes." in report


def test_render_final_report_blocked_labels_blockers(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    failed = log.append("tool", {"event": "finished", "tool": "terminal", "fx": "verify", "ok": False})
    rows = log.read_all()
    result = evaluate(_task(), rows)

    report = render_final_report(_task(), rows, result, "Done.")

    assert "Status: blocked" in report
    assert "Missing acceptance IDs: ac1, ac2" in report
    assert f"Blocking IDs: {failed.s}" in report
    assert "Conclusion: Blocked." in report


def test_render_final_report_unverified_labels_missing_evidence(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("run", {"event": "created", "ok": True})
    rows = log.read_all()
    result = evaluate(_task(), rows)

    report = render_final_report(_task(), rows, result, "I think this is complete.")

    assert "Status: unverified" in report
    assert "Complete acceptance IDs: none" in report
    assert "Missing acceptance IDs: ac1, ac2" in report
    assert "Conclusion: Unverified." in report
