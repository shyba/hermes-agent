"""Contract tests for harness plugin-local configuration."""

from __future__ import annotations

import pytest

from plugins.harness.config import (
    DEFAULT_TOMBSTONE_MAX_RESULT_CHARS,
    DEFAULT_TOMBSTONE_PREVIEW_CHARS,
    HarnessConfigError,
    load_config,
)


def test_defaults_are_conservative_and_env_free(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_HARNESS_ENABLED", "1")
    monkeypatch.setenv("HARNESS_TOMBSTONE_MAX_RESULT_CHARS", "42")

    config = load_config()

    assert config.enabled is False
    assert config.tombstone.max_result_chars == DEFAULT_TOMBSTONE_MAX_RESULT_CHARS
    assert config.tombstone.preview_chars == DEFAULT_TOMBSTONE_PREVIEW_CHARS
    assert config.artifacts.capture is True
    assert config.final_report.enabled is True
    assert config.manager.enabled is True
    assert config.manager.model is None
    assert config.architect.enabled is False
    assert config.architect.model is None


def test_nested_harness_config_overrides_defaults():
    config = load_config(
        {
            "harness": {
                "enabled": True,
                "tombstone": {
                    "max_result_chars": 64000,
                    "preview_chars": 2400,
                },
                "artifacts": {"capture": False},
                "final_report": {"enabled": False},
                "manager": {"enabled": False, "model": "manager-model"},
                "architect": {"enabled": True, "model": "architect-model"},
            }
        }
    )

    assert config.enabled is True
    assert config.tombstone.max_result_chars == 64000
    assert config.tombstone.preview_chars == 2400
    assert config.artifacts.capture is False
    assert config.final_report.enabled is False
    assert config.manager.enabled is False
    assert config.manager.model == "manager-model"
    assert config.architect.enabled is True
    assert config.architect.model == "architect-model"


def test_hermes_config_shape_is_supported_and_explicit_source_wins():
    config = load_config(
        {"tombstone": {"preview_chars": 3000}, "manager": {"model": "specific-manager"}},
        hermes_config={
            "display": {"skin": "mono"},
            "plugins": {
                "harness": {
                    "enabled": True,
                    "tombstone": {
                        "max_result_chars": 32000,
                        "preview_chars": 2000,
                    },
                    "manager": {"model": "global-manager"},
                }
            },
        },
    )

    assert config.enabled is True
    assert config.tombstone.max_result_chars == 32000
    assert config.tombstone.preview_chars == 3000
    assert config.manager.model == "specific-manager"


def test_bad_integer_values_are_clamped_to_supported_bounds():
    too_small = load_config({"tombstone": {"max_result_chars": 1, "preview_chars": -10}})
    too_large = load_config({"tombstone": {"max_result_chars": 999999999, "preview_chars": 999999}})

    assert too_small.tombstone.max_result_chars == 1000
    assert too_small.tombstone.preview_chars == 0
    assert too_large.tombstone.max_result_chars == 500000
    assert too_large.tombstone.preview_chars == 10000


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"enabled": "true"}, "enabled must be a boolean"),
        ({"tombstone": {"max_result_chars": "12000"}}, "tombstone.max_result_chars must be an integer"),
        ({"artifacts": {"capture": 1}}, "artifacts.capture must be a boolean"),
        ({"final_report": {"enabled": "no"}}, "final_report.enabled must be a boolean"),
        ({"manager": {"model": 123}}, "manager.model must be a string"),
        ({"architect": {"enabled": "yes"}}, "architect.enabled must be a boolean"),
    ],
)
def test_bad_value_types_are_rejected(source, message):
    with pytest.raises(HarnessConfigError, match=message):
        load_config(source)
