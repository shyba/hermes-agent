"""Contract tests for the harness context engine plugin."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins.context_engine import load_context_engine
from plugins.context_engine.harness import HarnessContextEngine


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_harness_context_engine_loads_from_plugin_registry():
    engine = load_context_engine("harness")

    assert isinstance(engine, HarnessContextEngine)
    assert engine.name == "harness"


def test_noop_when_there_are_no_repeated_large_tool_results(git_repo: Path):
    engine = HarnessContextEngine(max_result_chars=20)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": "a" * 25},
        {"role": "tool", "tool_call_id": "call-2", "name": "terminal", "content": "b" * 25},
    ]

    compacted = engine.compress(messages)

    assert compacted == messages
    assert compacted is not messages
    assert all(out is original for out, original in zip(compacted, messages))
    assert engine.compression_count == 0
    assert not (git_repo / ".hermes-harness" / "context" / "artifacts").exists()


def test_tombstones_repeated_large_tool_output(git_repo: Path):
    engine = HarnessContextEngine(max_result_chars=40)
    result = "0123456789" * 8
    messages = [
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": result},
        {"role": "assistant", "content": "again"},
        {"role": "tool", "tool_call_id": "call-2", "name": "terminal", "content": result},
    ]

    compacted = engine.compress(messages)

    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[2]["tool_call_id"] == "call-2"
    assert compacted[2]["name"] == "terminal"
    assert compacted[2]["content"] != result

    tombstone = json.loads(compacted[2]["content"])
    assert tombstone["harness_tombstone"] is True
    assert tombstone["tool"] == "terminal"
    assert tombstone["original_chars"] == len(result)
    assert tombstone["preview"] == result[:40]
    assert (git_repo / tombstone["artifact_path"]).read_text(encoding="utf-8") == result
    assert engine.compression_count == 1


def test_preserves_user_and_system_messages(git_repo: Path):
    engine = HarnessContextEngine(max_result_chars=10)
    repeated = "x" * 20
    system = {"role": "system", "content": repeated}
    user = {"role": "user", "content": repeated}
    messages = [
        system,
        {"role": "tool", "tool_call_id": "call-1", "content": repeated},
        user,
        {"role": "tool", "tool_call_id": "call-2", "content": repeated},
    ]

    compacted = engine.compress(messages)

    assert compacted[0] is system
    assert compacted[2] is user
    assert compacted[0] == system
    assert compacted[2] == user
    assert json.loads(compacted[3]["content"])["harness_tombstone"] is True


def test_output_is_deterministic(git_repo: Path):
    result = "same output\n" * 12
    messages = [
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": result},
        {"role": "tool", "tool_call_id": "call-2", "name": "terminal", "content": result},
    ]

    first = HarnessContextEngine(max_result_chars=30).compress(messages)
    second = HarnessContextEngine(max_result_chars=30).compress(messages)

    assert first == second
    assert json.loads(first[1]["content"]) == json.loads(second[1]["content"])
