"""One place that defines how artifacts and diffs are hashed, so every binding agrees."""
import hashlib
import json


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value):
    """Hash canonical JSON so key order and whitespace never change an artifact's identity."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)
