"""Tests for CLI redraw helpers used to recover from terminal buffer drift.

Covers:
  - _force_full_redraw (#8688 cmux tab switch, /redraw, Ctrl+L)
  - the resize handler we install over prompt_toolkit's _on_resize (#5474)

Both behaviors are exercised against fake prompt_toolkit renderer/output
objects.  For manual redraw we assert the full clear sequence; for resize
we assert that the full clear sequence is not sent.
"""

from unittest.mock import MagicMock

import pytest

from cli import HermesCLI


@pytest.fixture
def bare_cli():
    """A HermesCLI with no __init__ — we only exercise the redraw helper."""
    cli = object.__new__(HermesCLI)
    return cli


class TestForceFullRedraw:
    def test_no_app_is_safe(self, bare_cli):
        # _force_full_redraw must be a no-op when the TUI isn't running.
        bare_cli._app = None
        bare_cli._force_full_redraw()  # must not raise

    def test_missing_app_attr_is_safe(self, bare_cli):
        # Simulate HermesCLI before the TUI has ever been constructed.
        bare_cli._force_full_redraw()  # must not raise

    def test_sends_full_clear_and_invalidates(self, bare_cli):
        app = MagicMock()
        out = app.renderer.output
        bare_cli._app = app

        bare_cli._force_full_redraw()

        # Must erase screen, home cursor, and flush — in that order.
        out.reset_attributes.assert_called_once()
        out.erase_screen.assert_called_once()
        out.cursor_goto.assert_called_once_with(0, 0)
        out.flush.assert_called_once()

        # Must reset prompt_toolkit's tracked screen/cursor state so the
        # next incremental redraw starts from a clean (0, 0) origin.
        app.renderer.reset.assert_called_once_with(leave_alternate_screen=False)

        # Must schedule a repaint.
        app.invalidate.assert_called_once()

    def test_swallows_renderer_exceptions(self, bare_cli):
        # If the renderer blows up for any reason, the helper must not
        # propagate — otherwise a stray Ctrl+L would crash the CLI.
        app = MagicMock()
        app.renderer.output.erase_screen.side_effect = RuntimeError("boom")
        bare_cli._app = app

        bare_cli._force_full_redraw()  # must not raise

        # invalidate() is still attempted after a renderer failure.
        app.invalidate.assert_called_once()

    def test_swallows_invalidate_exceptions(self, bare_cli):
        app = MagicMock()
        app.invalidate.side_effect = RuntimeError("boom")
        bare_cli._app = app

        bare_cli._force_full_redraw()  # must not raise


class TestResizeHandler:
    def test_resize_preserves_terminal_history(self, bare_cli):
        app = MagicMock()
        original_on_resize = MagicMock()
        app._on_resize = original_on_resize

        bare_cli._install_resize_handler(app)
        app._on_resize()

        original_on_resize.assert_called_once()
        app.invalidate.assert_called_once()
        app.renderer.reset.assert_not_called()

        out = app.renderer.output
        out.reset_attributes.assert_not_called()
        out.erase_screen.assert_not_called()
        out.cursor_goto.assert_not_called()
        out.flush.assert_not_called()

    def test_resize_swallows_invalidate_exceptions(self, bare_cli):
        app = MagicMock()
        original_on_resize = MagicMock()
        app._on_resize = original_on_resize
        app.invalidate.side_effect = RuntimeError("invalidate failed")

        bare_cli._install_resize_handler(app)
        app._on_resize()  # must not raise

        original_on_resize.assert_called_once()
