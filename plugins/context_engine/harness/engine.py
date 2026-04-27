"""Deterministic context compaction for harness runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent.context_engine import ContextEngine
from plugins.harness import context as harness_context


class HarnessContextEngine(ContextEngine):
    """Compact repeat-heavy harness transcripts without model calls."""

    threshold_percent = 0.75
    protect_first_n = 0
    protect_last_n = 0

    def __init__(self, *, max_result_chars: int = harness_context.DEFAULT_MAX_RESULT_CHARS):
        self.max_result_chars = max_result_chars
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

    @property
    def name(self) -> str:
        return "harness"

    def is_available(self) -> bool:
        return True

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = _int_usage(usage, "prompt_tokens")
        self.last_completion_tokens = _int_usage(usage, "completion_tokens")
        total = _int_usage(usage, "total_tokens")
        self.last_total_tokens = total or self.last_prompt_tokens + self.last_completion_tokens

    def should_compress(self, prompt_tokens: int = None) -> bool:
        tokens = self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        return bool(self.threshold_tokens and tokens >= self.threshold_tokens)

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        return self.has_content_to_compress(messages)

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        seen: set[str] = set()
        for message in messages:
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if _is_harness_tombstone(content):
                continue
            if not harness_context.should_tombstone_result(content, self.max_result_chars):
                continue
            digest = _digest(content)
            if digest in seen:
                return True
            seen.add(digest)
        return False

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        compacted: list[dict[str, Any]] = []
        changed = False

        for message in messages:
            replacement = self._compact_message(message, seen)
            if replacement is message:
                compacted.append(message)
                continue
            compacted.append(replacement)
            changed = True

        if changed:
            self.compression_count += 1
        return compacted

    def _compact_message(
        self,
        message: dict[str, Any],
        seen: set[str],
    ) -> dict[str, Any]:
        if message.get("role") != "tool":
            return message

        content = message.get("content")
        if not isinstance(content, str):
            return message
        if _is_harness_tombstone(content):
            return message
        if not harness_context.should_tombstone_result(content, self.max_result_chars):
            return message

        digest = _digest(content)
        if digest not in seen:
            seen.add(digest)
            return message

        tool_name = str(message.get("name") or "tool")
        args = _tool_args(message)
        replacement, metadata = harness_context.tombstone_tool_result(
            tool_name,
            args,
            content,
            max_chars=self.max_result_chars,
        )
        if not metadata.get("tombstoned"):
            return message

        updated = dict(message)
        updated["content"] = replacement
        return updated


def _int_usage(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0) if isinstance(usage, dict) else 0
    return value if isinstance(value, int) else 0


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_harness_tombstone(content: str) -> bool:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("harness_tombstone") is True


def _tool_args(message: dict[str, Any]) -> dict[str, Any] | None:
    args = message.get("args")
    if isinstance(args, dict):
        return args
    return None
