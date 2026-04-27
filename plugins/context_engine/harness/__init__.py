"""Harness context engine plugin."""

from .engine import HarnessContextEngine


def register(ctx):
    ctx.register_context_engine(HarnessContextEngine())


__all__ = ["HarnessContextEngine", "register"]
