"""Contract tests for harness context tombstones."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins.harness import context


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_small_tool_result_is_unchanged_and_not_stored(git_repo: Path):
    result = "short terminal output"

    transformed, metadata = context.tombstone_tool_result(
        "terminal",
        {"command": "printf ok"},
        result,
        repo_root=git_repo,
        max_chars=100,
    )

    assert context.should_tombstone_result(result, max_chars=100) is False
    assert context.result_preview(result) == result
    assert transformed == result
    assert metadata == {
        "tombstoned": False,
        "artifact_path": None,
        "original_chars": len(result),
        "preview_chars": len(result),
    }
    assert not (git_repo / ".hermes-harness" / "context" / "artifacts").exists()


def test_large_tool_result_stores_artifact_and_returns_compact_json_tombstone(git_repo: Path):
    result = "x" * 160

    transformed, metadata = context.tombstone_tool_result(
        "terminal",
        {"command": "cat huge.log"},
        result,
        repo_root=git_repo,
        max_chars=80,
    )

    assert context.should_tombstone_result(result, max_chars=80) is True
    tombstone = json.loads(transformed)
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    artifact_path = git_repo / ".hermes-harness" / "context" / "artifacts" / f"{digest}.txt"

    assert artifact_path.read_text(encoding="utf-8") == result
    assert tombstone == {
        "args": {"command": "cat huge.log"},
        "artifact_name": f"{digest}.txt",
        "artifact_path": f".hermes-harness/context/artifacts/{digest}.txt",
        "harness_tombstone": True,
        "original_chars": len(result),
        "preview": result[:80],
        "sha256": digest,
        "tool": "terminal",
    }
    assert metadata == {
        "tombstoned": True,
        "artifact_path": f".hermes-harness/context/artifacts/{digest}.txt",
        "original_chars": len(result),
        "preview_chars": 80,
    }


def test_tombstone_preview_is_bounded_by_smaller_max_chars(git_repo: Path):
    result = "0123456789" * 20

    transformed, metadata = context.tombstone_tool_result(
        "terminal",
        {"command": "cat huge.log"},
        result,
        repo_root=git_repo,
        max_chars=40,
    )

    tombstone = json.loads(transformed)
    assert tombstone["preview"] == result[:40]
    assert tombstone["preview"] != result
    assert result not in transformed
    assert metadata["preview_chars"] == 40
