"""Control contract parsing for deterministic harness runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HarnessControlsError(ValueError):
    """Raised when harness controls do not match the contract."""


@dataclass(frozen=True)
class SensorSpec:
    id: str
    kind: str = "ledger"
    required: bool = True
    command: str | None = None
    covers: tuple[str, ...] = ()
    trigger: str | None = None
    freshness: str | None = None
    config: dict[str, Any] | None = None


@dataclass(frozen=True)
class HarnessControls:
    version: str
    sensors: tuple[SensorSpec, ...]
    raw: dict[str, Any] | None = None

    @property
    def sensor_ids(self) -> list[str]:
        return [sensor.id for sensor in self.sensors]


def load_controls(path: str | Path) -> HarnessControls:
    """Load and validate harness controls from YAML."""

    yaml = _yaml()
    controls_path = Path(path)
    try:
        data = yaml.safe_load(controls_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HarnessControlsError(f"could not read controls file {controls_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise HarnessControlsError(f"invalid YAML in controls file {controls_path}: {exc}") from exc
    return parse_controls(data)


def parse_controls(data: dict[str, Any]) -> HarnessControls:
    """Validate and normalize a harness controls dictionary."""

    if not isinstance(data, dict):
        raise HarnessControlsError("controls must be a mapping")

    version = _required_nonempty_string(data, "version")
    sensors_raw = data.get("sensors")
    if not isinstance(sensors_raw, list) or not sensors_raw:
        raise HarnessControlsError("sensors must be a non-empty list")

    seen: set[str] = set()
    sensors: list[SensorSpec] = []
    for index, item in enumerate(sensors_raw):
        if not isinstance(item, dict):
            raise HarnessControlsError(f"sensors[{index}] must be a mapping")
        sensor_id = _required_nonempty_string(item, "id", f"sensors[{index}].id")
        if sensor_id in seen:
            raise HarnessControlsError(f"sensor id must be unique: {sensor_id}")
        seen.add(sensor_id)

        kind = item.get("kind")
        if kind is None:
            kind = "command" if item.get("command") is not None else "ledger"
        if not _is_nonempty_string(kind):
            raise HarnessControlsError(f"sensors[{index}].kind must be a non-empty string")

        required = item.get("required", True)
        if not isinstance(required, bool):
            raise HarnessControlsError(f"sensors[{index}].required must be a boolean")

        command = item.get("command")
        if command is not None and not _is_nonempty_string(command):
            raise HarnessControlsError(f"sensors[{index}].command must be a non-empty string when set")

        covers = _string_tuple(item.get("covers"), f"sensors[{index}].covers")

        trigger = item.get("trigger")
        if trigger is not None and not _is_nonempty_string(trigger):
            raise HarnessControlsError(f"sensors[{index}].trigger must be a non-empty string when set")

        freshness = item.get("freshness")
        if freshness is not None and not _is_nonempty_string(freshness):
            raise HarnessControlsError(f"sensors[{index}].freshness must be a non-empty string when set")

        extra = {
            key: value for key, value in item.items()
            if key not in {"id", "kind", "required", "command", "covers", "trigger", "freshness", "config"}
        }
        config = item.get("config")
        if config is not None and not isinstance(config, dict):
            raise HarnessControlsError(f"sensors[{index}].config must be a mapping when set")
        merged_config = dict(config) if config is not None else {}
        merged_config.update(extra)

        sensors.append(
            SensorSpec(
                id=sensor_id,
                kind=kind,
                required=required,
                command=command,
                covers=covers,
                trigger=trigger,
                freshness=freshness,
                config=merged_config or None,
            )
        )

    return HarnessControls(version=version, sensors=tuple(sensors), raw=dict(data))


def default_controls() -> HarnessControls:
    """Return the built-in ledger sensor controls for the MVP."""

    sensor_ids = (
        "generic.claims_have_evidence",
        "generic.verification_freshness",
        "generic.no_done_after_timeout",
        "generic.no_read_only_violation",
        "generic.no_unresolved_failed_tools",
    )
    return HarnessControls(
        version="1",
        sensors=tuple(SensorSpec(id=sensor_id, kind="ledger") for sensor_id in sensor_ids),
        raw={
            "version": "1",
            "sensors": [{"id": sensor_id, "kind": "ledger", "required": True} for sensor_id in sensor_ids],
        },
    )


def _required_nonempty_string(data: dict[str, Any], key: str, field: str | None = None) -> str:
    value = data.get(key)
    if not _is_nonempty_string(value):
        raise HarnessControlsError(f"{field or key} must be a non-empty string")
    return value


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise HarnessControlsError(f"{field} must be a string or list of strings")


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise HarnessControlsError("PyYAML is required to load harness controls files") from exc
    return yaml
