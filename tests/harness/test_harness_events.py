"""Contract tests for the harness event stream."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from threading import Thread

import pytest


def _load_events_module():
    return importlib.import_module("plugins.harness.events")


def _event_log_path(run_dir: Path) -> Path:
    return run_dir / "events.jsonl"


def test_appending_events_assigns_monotonic_sequence_numbers(tmp_path):
    events = _load_events_module()
    run_dir = tmp_path / "runs" / "harness-0001"
    run_dir.mkdir(parents=True)
    log = events.EventLog(_event_log_path(run_dir))

    log.append("run", {"event": "created", "run": "harness-0001", "ok": True})
    log.append("intent", {"latest": "ship focused tests", "ok": True})
    log.append("finalization", {"status": "blocked", "blocking": ["ev2"]})

    loaded = list(events.read_events(_event_log_path(run_dir)))
    assert [event.s for event in loaded] == [1, 2, 3]
    assert [event.t for event in loaded] == ["run", "intent", "finalization"]
    assert loaded[0].event == "created"
    assert loaded[0].payload["run"] == "harness-0001"
    assert loaded[2].payload["blocking"] == ["ev2"]
    first_line = _event_log_path(run_dir).read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith('{"s":1,"t":"run",')


def test_concurrent_event_logs_do_not_duplicate_sequence_numbers(tmp_path):
    events = _load_events_module()
    log_path = _event_log_path(tmp_path / "runs" / "harness-0001")

    def append_one(index: int) -> None:
        events.EventLog(log_path).append("tool", {"event": "finished", "index": index})

    workers = [Thread(target=append_one, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    loaded = list(events.read_events(log_path))
    assert [event.s for event in loaded] == list(range(1, 9))
    assert sorted(event.payload["index"] for event in loaded) == list(range(8))


def test_reading_events_reports_corrupt_jsonl_record(tmp_path):
    events = _load_events_module()
    run_dir = tmp_path / "runs" / "harness-0001"
    run_dir.mkdir(parents=True)
    _event_log_path(run_dir).write_text(
        '{"s":1,"t":"run","event":"created","ok":true}\n'
        '{"s":2,"t":"intent","ok":true\n',
        encoding="utf-8",
    )

    corruption_error = events.EventLogCorruptionError
    with pytest.raises(corruption_error):
        list(events.read_events(_event_log_path(run_dir)))


@pytest.mark.parametrize(
    "line",
    [
        "",
        "[]",
        '{"t":"run","event":"created"}',
        '{"s":"1","t":"run","event":"created"}',
        '{"s":0,"t":"run","event":"created"}',
        '{"s":1,"event":"created"}',
        '{"s":1,"t":"","event":"created"}',
        '{"s":1,"t":"run","event":"created"}\n{"s":1,"t":"run","event":"again"}',
        '{"s":2,"t":"run","event":"created"}',
    ],
)
def test_reading_events_reports_schema_and_sequence_corruption(tmp_path, line):
    events = _load_events_module()
    log_path = _event_log_path(tmp_path / "runs" / "harness-0001")
    log_path.parent.mkdir(parents=True)
    log_path.write_text(f"{line}\n", encoding="utf-8")

    with pytest.raises(events.EventLogCorruptionError):
        list(events.read_events(log_path))


def test_fixture_event_logs_parse_and_express_expected_statuses():
    events = _load_events_module()
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "harness"

    complete = list(events.read_events(fixture_root / "events-complete.jsonl"))
    blocked = list(events.read_events(fixture_root / "events-blocked.jsonl"))

    assert complete[-1].t == "finalization"
    assert complete[-1].payload["status"] == "complete"
    assert blocked[-1].t == "finalization"
    assert blocked[-1].payload["status"] == "blocked"


def test_serialized_event_log_is_line_oriented_top_level_json(tmp_path):
    events = _load_events_module()
    log_path = _event_log_path(tmp_path / "runs" / "harness-0001")

    event = events.EventLog(log_path).append(
        "run",
        {"event": "created", "run": "harness-0001", "ok": True},
    )

    raw = json.loads(log_path.read_text(encoding="utf-8"))
    assert raw["s"] == event.s == 1
    assert raw["t"] == event.t == "run"
    assert raw["event"] == "created"
    assert raw["run"] == "harness-0001"
    assert raw["ok"] is True
    assert isinstance(raw["ts"], str) and raw["ts"].endswith("Z")
