"""Logging callback handler for Strands agents — logs tool calls and LLM responses."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("nubi.agent")

# Flush text buffer every N chunks to show progress during long responses
_FLUSH_INTERVAL = 50


class LoggingCallbackHandler:
    """Callback handler that logs tool calls, tool results, and LLM text to Python logging."""

    def __init__(self) -> None:
        self._current_tool: str | None = None
        self._text_buffer: list[str] = []
        self._chunk_count: int = 0

    def __call__(self, **kwargs: Any) -> None:
        event = kwargs.get("event", {})

        # Tool call start
        content_block_start = event.get("contentBlockStart", {})
        tool_use = content_block_start.get("start", {}).get("toolUse")
        if tool_use:
            name = tool_use.get("name", "?")
            self._current_tool = name
            self._flush_text()
            logger.info("tool_call: %s", name)

        # Current tool use with full input (available in kwargs)
        current_tool_use = kwargs.get("current_tool_use")
        if current_tool_use and isinstance(current_tool_use, dict):
            name = current_tool_use.get("name", "")
            tool_input = current_tool_use.get("input", {})
            if name and tool_input:
                try:
                    input_str = json.dumps(tool_input, default=str)
                    if len(input_str) > 500:
                        input_str = input_str[:500] + "..."
                    logger.info("tool_input: %s(%s)", name, input_str)
                except (TypeError, ValueError):
                    pass

        # Text response chunks
        data = kwargs.get("data")
        if data and isinstance(data, str):
            self._text_buffer.append(data)
            self._chunk_count += 1
            if self._chunk_count >= _FLUSH_INTERVAL:
                self._flush_text()
                self._chunk_count = 0

        # Block complete — flush remaining text
        complete = kwargs.get("complete", False)
        if complete:
            self._flush_text()
            self._current_tool = None
            self._chunk_count = 0

    def _flush_text(self) -> None:
        if self._text_buffer:
            text = "".join(self._text_buffer).strip()
            if text:
                if len(text) > 1000:
                    text = text[:1000] + "..."
                logger.info("llm_text: %s", text)
            self._text_buffer.clear()
