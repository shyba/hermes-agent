"""Context artifact and tombstone helpers for harness hooks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import paths


DEFAULT_MAX_RESULT_CHARS = 12000
DEFAULT_PREVIEW_CHARS = 1200


def should_tombstone_result(
    result: str,
    max_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> bool:
    """Return True when a tool result should be replaced with an artifact tombstone."""

    return len(result) > max_chars


def result_preview(result: str, limit: int = DEFAULT_PREVIEW_CHARS) -> str:
    """Return a deterministic compact preview of a result string."""

    if limit <= 0:
        return ""
    return result[:limit]


def tombstone_tool_result(
    tool_name: str,
    args: dict[str, Any] | None,
    result: str,
    repo_root: str | os.PathLike[str] | None = None,
    max_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Store oversized tool results as artifacts and return a JSON tombstone."""

    preview_limit = min(DEFAULT_PREVIEW_CHARS, max(max_chars, 0))
    preview = result_preview(result, limit=preview_limit)
    original_chars = len(result)

    if not should_tombstone_result(result, max_chars=max_chars):
        return result, {
            "tombstoned": False,
            "artifact_path": None,
            "original_chars": original_chars,
            "preview_chars": len(preview),
        }

    artifact_path = paths.store_artifact(result, repo_root, suffix=".txt")
    artifact_ref = _relative_artifact_path(artifact_path, repo_root)
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    tombstone = {
        "harness_tombstone": True,
        "tool": tool_name,
        "artifact_path": artifact_ref,
        "artifact_name": artifact_path.name,
        "original_chars": original_chars,
        "preview": preview,
        "sha256": digest,
    }
    if args is not None:
        tombstone["args"] = args

    return json.dumps(tombstone, sort_keys=True), {
        "tombstoned": True,
        "artifact_path": artifact_ref,
        "original_chars": original_chars,
        "preview_chars": len(preview),
    }


def _relative_artifact_path(
    artifact_path: Path,
    repo_root: str | os.PathLike[str] | None,
) -> str:
    root = paths.find_repo_root(repo_root) if repo_root is None else Path(repo_root).resolve()
    try:
        return artifact_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(artifact_path)
