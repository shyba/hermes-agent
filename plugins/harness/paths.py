"""Filesystem primitives for Hermes harness runs."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HARNESS_DIRNAME = ".hermes-harness"
RUNS_DIRNAME = "runs"
CONTEXT_DIRNAME = "context"
ARTIFACTS_DIRNAME = "artifacts"
ACTIVE_RUN_FILENAME = "active-run.json"
GITIGNORE_FILENAME = ".gitignore"

_GITIGNORE_CONTENT = """# Hermes harness runtime state
/runs/
/context/artifacts/
"""


class HarnessPathError(RuntimeError):
    """Raised when harness paths cannot be resolved or initialized."""


@dataclass(frozen=True)
class HarnessPaths:
    """Resolved path set for a repository's harness state."""

    repo_root: Path
    harness_root: Path
    runs_dir: Path
    context_dir: Path
    artifacts_dir: Path
    active_run_file: Path


def find_repo_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Return the nearest ancestor that looks like the repository root."""

    current = Path(start or os.getcwd()).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "pyproject.toml").is_file() and (candidate / "plugins").is_dir():
            return candidate

    raise HarnessPathError(f"Could not find repository root from {current}")


def harness_paths(
    repo_root: str | os.PathLike[str] | None = None,
    *,
    create: bool = True,
) -> HarnessPaths:
    """Resolve harness paths, creating the runtime directory layout by default."""

    root = find_repo_root(repo_root) if repo_root is None else Path(repo_root).resolve()
    harness_root = root / HARNESS_DIRNAME
    paths = HarnessPaths(
        repo_root=root,
        harness_root=harness_root,
        runs_dir=harness_root / RUNS_DIRNAME,
        context_dir=harness_root / CONTEXT_DIRNAME,
        artifacts_dir=harness_root / CONTEXT_DIRNAME / ARTIFACTS_DIRNAME,
        active_run_file=harness_root / ACTIVE_RUN_FILENAME,
    )

    if create:
        ensure_harness(paths)

    return paths


def ensure_harness(paths: HarnessPaths | None = None) -> HarnessPaths:
    """Create the harness runtime directories and narrow local ignore file."""

    paths = paths or harness_paths(create=False)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    paths.context_dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(paths.harness_root / GITIGNORE_FILENAME)
    return paths


def generate_run_id(now: datetime | None = None) -> str:
    """Generate a sortable run id without randomness or external state."""

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return stamp.strftime("run-%Y%m%dT%H%M%S.%fZ")


def generate_unique_run_id(
    repo_root: str | os.PathLike[str] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """Generate a sortable run id that does not already have a run directory."""

    base = generate_run_id(now)
    paths = harness_paths(repo_root)
    candidate = base
    suffix = 2
    while (paths.runs_dir / candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def run_dir(
    run_id: str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    create: bool = True,
) -> Path:
    """Return the directory for a run id."""

    _validate_run_id(run_id)
    return harness_paths(repo_root, create=create).runs_dir / run_id


def ensure_run_dir(run_id: str, repo_root: str | os.PathLike[str] | None = None) -> Path:
    """Create and return the directory for a run id."""

    path = run_dir(run_id, repo_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_active_run(
    run_id: str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Persist the active harness run marker atomically."""

    _validate_run_id(run_id)
    paths = harness_paths(repo_root)
    ensure_run_dir(run_id, paths.repo_root)
    payload = {
        "run_id": run_id,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if metadata:
        payload["metadata"] = metadata
    _atomic_write_json(paths.active_run_file, payload)
    return paths.active_run_file


def get_active_run(repo_root: str | os.PathLike[str] | None = None) -> str | None:
    """Return the active run id, or None when no active run is recorded."""

    path = harness_paths(repo_root, create=False).active_run_file
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessPathError(f"Invalid active run marker {path}: {exc}") from exc

    run_id = data.get("run_id")
    if not isinstance(run_id, str):
        raise HarnessPathError(f"Invalid active run marker {path}: missing run_id")
    _validate_run_id(run_id)
    return run_id


def clear_active_run(repo_root: str | os.PathLike[str] | None = None) -> None:
    """Remove the active run marker if it exists."""

    path = harness_paths(repo_root, create=False).active_run_file
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def store_artifact(
    content: bytes | str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    suffix: str = "",
) -> Path:
    """Store an artifact by sha256 content hash and return its path."""

    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    digest = hashlib.sha256(raw).hexdigest()
    clean_suffix = _clean_suffix(suffix)
    paths = harness_paths(repo_root)
    path = paths.artifacts_dir / f"{digest}{clean_suffix}"
    if not path.exists():
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    return path


def _ensure_gitignore(path: Path) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == _GITIGNORE_CONTENT:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_GITIGNORE_CONTENT, encoding="utf-8")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _validate_run_id(run_id: str) -> None:
    if not run_id or run_id in {".", ".."}:
        raise HarnessPathError("run_id must be a non-empty path segment")
    if "/" in run_id or "\\" in run_id:
        raise HarnessPathError(f"run_id must not contain path separators: {run_id!r}")


def _clean_suffix(suffix: str) -> str:
    if not suffix:
        return ""
    if "/" in suffix or "\\" in suffix:
        raise HarnessPathError(f"artifact suffix must not contain path separators: {suffix!r}")
    return suffix if suffix.startswith(".") else f".{suffix}"
