"""Who may sign a decision: a named human. The check is a guard against obvious AI identities, not proof."""
import re

AI_IDENTITIES = ("claude", "gpt", "llm", "assistant", "bot", "ai", "agent", "codex", "gemini")
_WORDS = re.compile(r"[a-z0-9]+")


class ReviewerError(ValueError):
    """The reviewer name is missing or looks like an AI."""


def validate_reviewer(name):
    """The trimmed name, or ReviewerError. Rejects empty names and names with an AI-identity word."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ReviewerError("a reviewer name is required (pass --reviewer or set $USER)")
    words = {word.rstrip("0123456789") for word in _WORDS.findall(cleaned.lower())}  # "gpt4" is still gpt
    flagged = sorted(words & set(AI_IDENTITIES))
    if flagged:
        raise ReviewerError(f"reviewer {cleaned!r} looks like an AI identity ({', '.join(flagged)}); a human must sign")
    return cleaned
