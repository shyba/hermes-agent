"""Harness plugin registration."""

from .command import handle_slash
from .continuation import register_dead_end_tool
from .hooks import register_hooks
from .validation import register_validation_tool


def register(ctx) -> None:
    ctx.register_command(
        "harness",
        handler=handle_slash,
        description="Manage deterministic harness runs.",
        args_hint="[help|status|run <task>|check]",
    )
    register_dead_end_tool(ctx)
    register_validation_tool(ctx)
    register_hooks(ctx)
