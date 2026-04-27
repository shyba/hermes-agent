"""Plugin-local configuration helpers for harness mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_ENABLED = False
DEFAULT_TOMBSTONE_MAX_RESULT_CHARS = 12000
DEFAULT_TOMBSTONE_PREVIEW_CHARS = 1200
DEFAULT_ARTIFACT_CAPTURE = True
DEFAULT_FINAL_REPORT = True
DEFAULT_MANAGER_ENABLED = True
DEFAULT_ARCHITECT_ENABLED = False
MIN_TOMBSTONE_MAX_RESULT_CHARS = 1000
MAX_TOMBSTONE_MAX_RESULT_CHARS = 500000
MIN_TOMBSTONE_PREVIEW_CHARS = 0
MAX_TOMBSTONE_PREVIEW_CHARS = 10000


class HarnessConfigError(ValueError):
    """Raised when harness configuration contains an invalid value."""


@dataclass(frozen=True)
class TombstoneConfig:
    max_result_chars: int = DEFAULT_TOMBSTONE_MAX_RESULT_CHARS
    preview_chars: int = DEFAULT_TOMBSTONE_PREVIEW_CHARS


@dataclass(frozen=True)
class ArtifactConfig:
    capture: bool = DEFAULT_ARTIFACT_CAPTURE


@dataclass(frozen=True)
class FinalReportConfig:
    enabled: bool = DEFAULT_FINAL_REPORT


@dataclass(frozen=True)
class ManagerConfig:
    enabled: bool = DEFAULT_MANAGER_ENABLED
    model: str | None = None


@dataclass(frozen=True)
class ArchitectConfig:
    enabled: bool = DEFAULT_ARCHITECT_ENABLED
    model: str | None = None


@dataclass(frozen=True)
class HarnessConfig:
    enabled: bool = DEFAULT_ENABLED
    tombstone: TombstoneConfig = TombstoneConfig()
    artifacts: ArtifactConfig = ArtifactConfig()
    final_report: FinalReportConfig = FinalReportConfig()
    manager: ManagerConfig = ManagerConfig()
    architect: ArchitectConfig = ArchitectConfig()


def load_config(
    source: Mapping[str, Any] | None = None,
    *,
    hermes_config: Mapping[str, Any] | None = None,
) -> HarnessConfig:
    """Load harness config from explicit dicts and optional Hermes-shaped config.

    ``source`` is treated as the most specific input. It may be either the harness
    config itself or a larger config object containing a ``harness`` key.
    ``hermes_config`` accepts the caller's full Hermes config shape, without this
    plugin importing or migrating core config.
    """

    merged: dict[str, Any] = {}
    for candidate in (_extract_harness_config(hermes_config), _extract_harness_config(source)):
        if candidate:
            _deep_update(merged, candidate)

    return HarnessConfig(
        enabled=_bool(merged.get("enabled", DEFAULT_ENABLED), "enabled"),
        tombstone=TombstoneConfig(
            max_result_chars=_clamped_int(
                _nested_get(merged, ("tombstone", "max_result_chars"), DEFAULT_TOMBSTONE_MAX_RESULT_CHARS),
                "tombstone.max_result_chars",
                MIN_TOMBSTONE_MAX_RESULT_CHARS,
                MAX_TOMBSTONE_MAX_RESULT_CHARS,
            ),
            preview_chars=_clamped_int(
                _nested_get(merged, ("tombstone", "preview_chars"), DEFAULT_TOMBSTONE_PREVIEW_CHARS),
                "tombstone.preview_chars",
                MIN_TOMBSTONE_PREVIEW_CHARS,
                MAX_TOMBSTONE_PREVIEW_CHARS,
            ),
        ),
        artifacts=ArtifactConfig(
            capture=_bool(_nested_get(merged, ("artifacts", "capture"), DEFAULT_ARTIFACT_CAPTURE), "artifacts.capture"),
        ),
        final_report=FinalReportConfig(
            enabled=_bool(
                _nested_get(merged, ("final_report", "enabled"), DEFAULT_FINAL_REPORT),
                "final_report.enabled",
            ),
        ),
        manager=ManagerConfig(
            enabled=_bool(_nested_get(merged, ("manager", "enabled"), DEFAULT_MANAGER_ENABLED), "manager.enabled"),
            model=_optional_str(_nested_get(merged, ("manager", "model"), None), "manager.model"),
        ),
        architect=ArchitectConfig(
            enabled=_bool(
                _nested_get(merged, ("architect", "enabled"), DEFAULT_ARCHITECT_ENABLED),
                "architect.enabled",
            ),
            model=_optional_str(_nested_get(merged, ("architect", "model"), None), "architect.model"),
        ),
    )


def _extract_harness_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        return {}

    if _looks_like_harness_config(config):
        return dict(config)

    harness = config.get("harness")
    if isinstance(harness, Mapping):
        return dict(harness)

    plugins = config.get("plugins")
    if isinstance(plugins, Mapping):
        plugin_harness = plugins.get("harness")
        if isinstance(plugin_harness, Mapping):
            return dict(plugin_harness)

    return {}


def _looks_like_harness_config(config: Mapping[str, Any]) -> bool:
    keys = {"enabled", "tombstone", "artifacts", "final_report", "manager", "architect"}
    return any(key in config for key in keys)


def _deep_update(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = dict(value) if isinstance(value, Mapping) else value


def _nested_get(config: Mapping[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = config
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HarnessConfigError(f"{field} must be a boolean")


def _clamped_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HarnessConfigError(f"{field} must be an integer")
    return min(max(value, minimum), maximum)


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise HarnessConfigError(f"{field} must be a string")
