"""LLM client: the dispatcher depends on the LlmClient protocol, never on the network.

FireworksClient is the one real implementation (OpenAI-compatible API). Tests inject a fake.
"""
import os
from dataclasses import dataclass, field
from typing import Protocol

from openai import OpenAI, OpenAIError

DEFAULT_BASE_URL = "https://api.fireworks.ai/inference/v1"
MAX_OUTPUT_TOKENS = 8192
REQUEST_TIMEOUT_SECONDS = 120
TEMPERATURE = 0  # reproducible evals are easier to verify


class LlmError(Exception):
    """The API call failed (network, auth, rate limit, malformed response)."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other):
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass(frozen=True)
class LlmReply:
    content: str | None
    tool_calls: tuple = field(default_factory=tuple)
    usage: Usage = field(default_factory=Usage)


class LlmClient(Protocol):
    def chat(self, model: str, messages: list, tools: list) -> LlmReply: ...


class FireworksClient:
    def __init__(self, api_key, base_url=DEFAULT_BASE_URL):
        self._api_key = api_key
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def from_env(cls):
        return cls(os.environ["FIREWORKS_API_KEY"], os.environ.get("FIREWORKS_BASE_URL") or DEFAULT_BASE_URL)

    def chat(self, model, messages, tools):
        options = {"tools": tools} if tools else {}  # an empty tools list is rejected by some models
        try:
            response = self._client.chat.completions.create(
                model=model, messages=messages, max_tokens=MAX_OUTPUT_TOKENS,
                temperature=TEMPERATURE, **options,
            )
            return self._to_reply(response)
        except OpenAIError as exc:
            raise LlmError(self._redact(f"{type(exc).__name__}: {exc}")) from exc

    def _redact(self, text):
        return text.replace(self._api_key, "***")

    @staticmethod
    def _to_reply(response):
        message = response.choices[0].message
        calls = tuple(ToolCall(c.id, c.function.name, c.function.arguments or "") for c in message.tool_calls or ())
        usage = Usage(response.usage.prompt_tokens or 0, response.usage.completion_tokens or 0) if response.usage else Usage()
        return LlmReply(message.content, calls, usage)
