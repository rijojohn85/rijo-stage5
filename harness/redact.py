"""Remove quoted bug-report text from agent output before the coordinator sees it.

Exact-quote redaction only. It removes (1) any verbatim occurrence of the given exact quotes
(the screen's evidence, word for word) and (2) every run of MIN_QUOTE_WORDS or more consecutive
words that also appears, word for word, in the report. Words compare lowercased, with
surrounding punctuation ignored. LIMIT: a paraphrase, a translation, or a quote shorter than
MIN_QUOTE_WORDS words that is not itself one of the exact quotes gets through. Pure functions,
no I/O.
"""
import re

MIN_QUOTE_WORDS = 6
MARKER = "[quoted report text removed]"
MIN_EXACT_QUOTE_CHARS = 3
_WORD = re.compile(r"\S+")
_EDGE_PUNCTUATION = ".,;:!?\"'`()[]{}<>*_-"


def redact_report_quotes(value, report_text, exact_quotes=()):
    """A copy of `value` with quoted report text replaced by MARKER (strings inside dicts and lists)."""
    windows = _windows(_words(report_text))
    quotes = sorted((q for q in exact_quotes if len(q) >= MIN_EXACT_QUOTE_CHARS), key=len, reverse=True)
    return _walk(value, lambda text: _remove_word_runs(_remove_exact(text, quotes), windows))


def _walk(value, redact_string):
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {key: _walk(item, redact_string) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, redact_string) for item in value]
    return value


def _remove_exact(text, quotes):
    for quote in quotes:
        text = text.replace(quote, MARKER)
    return text


def _words(text):
    return [_normalise(w) for w in text.split()]


def _normalise(word):
    return word.strip(_EDGE_PUNCTUATION).lower()


def _windows(words):
    return {tuple(words[i:i + MIN_QUOTE_WORDS]) for i in range(len(words) - MIN_QUOTE_WORDS + 1)}


def _remove_word_runs(text, windows):
    tokens = list(_WORD.finditer(text))
    normalised = [_normalise(t.group()) for t in tokens]
    covered = [False] * len(tokens)
    for i in range(len(tokens) - MIN_QUOTE_WORDS + 1):
        if tuple(normalised[i:i + MIN_QUOTE_WORDS]) in windows:
            covered[i:i + MIN_QUOTE_WORDS] = [True] * MIN_QUOTE_WORDS
    return _rebuild(text, tokens, covered)


def _rebuild(text, tokens, covered):
    """Replace each maximal covered stretch of tokens, whitespace between them included, with one MARKER."""
    parts, position, i = [], 0, 0
    while i < len(tokens):
        if not covered[i]:
            i += 1
            continue
        end = i
        while end + 1 < len(tokens) and covered[end + 1]:
            end += 1
        parts += [text[position:tokens[i].start()], MARKER]
        position, i = tokens[end].end(), end + 1
    parts.append(text[position:])
    return "".join(parts)
