"""Shared Anthropic client setup.

One place to change the model or client options for every agent in the system.
"""

from __future__ import annotations

import os

import anthropic

MODEL = os.environ.get("SCRIPTSYNC_MODEL", "claude-opus-5")

# Non-streaming ceiling that keeps responses inside the SDK's HTTP timeout.
MAX_TOKENS = 16000

_client: anthropic.Anthropic | None = None
_async_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.Anthropic:
    """Sync client, reused across calls so connections stay warm."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def get_async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic()
    return _async_client


def text_of(response: anthropic.types.Message) -> str:
    """Concatenate the text blocks of a response, ignoring thinking blocks."""
    return "".join(block.text for block in response.content if block.type == "text")
