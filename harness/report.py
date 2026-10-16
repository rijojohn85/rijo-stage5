"""Report text written by code: headings, PASS/FAIL labels, the result line, and the failure report.

The writer model only supplies prose under headings. Everything here is a pure function of facts
that code computed from artifacts and the audit log (Section 6 of the plan), so no prose can
change a label. Labels follow a strict precedence: a step never attempted is NOT RUN; a failed
step is FAIL; an outdated artifact is STALE; a failed test is FAIL; then the review decides
(none = UNREVIEWED, outdated = STALE, revise/escalate = FAIL, pass = PASS). Only a fresh passing
review can produce PASS. The escalation brief is never PASS: it is AWAITING HUMAN.
"""
import re
from dataclasses import dataclass

PASS, FAIL, UNREVIEWED, STALE = "PASS", "FAIL", "UNREVIEWED", "STALE"
NOT_RUN, AWAITING_HUMAN = "NOT RUN", "AWAITING HUMAN"
HUMAN_BANNER = "**Human decision required**"
RESULT_PREFIX = "# Result: "
REVIEW_NONE, REVIEW_STALE, REVIEW_PASS = "none", "stale", "pass"

# section key -> heading title; LABELLED_SECTIONS maps the four step sections to the agent whose label they carry
SECTION_TITLES = {
    "overview": "Overview", "screen": "Screen", "triage": "Triage", "plan": "Plan", "implement": "Implement",
    "escalation": "Escalation", "review": "Review",
}
SECTION_ORDER = tuple(SECTION_TITLES)
LABELLED_SECTIONS = {"screen": "screen", "triage": "triage", "plan": "plan", "implement": "implement",
                     "escalation": "escalate"}
OPTIONAL_STEPS = ("screen", "escalate")  # off the status list when NOT RUN (the escalation is off the normal path)
FLAGGED = "FLAGGED"
EVIDENCE_INTRO = "Evidence quoted from the report:"
MIN_FENCE = 3
_HEADING = re.compile(r"^#{1,6}(\s|$)")
_SECTION_HEADING = re.compile(r"^##(\s|$)")
_FENCE_OPEN = re.compile(r"^(`{3,})")
_BACKTICK_RUN = re.compile(r"`+")
_RESULT_LINE = re.compile(rf"^{re.escape(RESULT_PREFIX)}(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class StepFacts:
    """What code knows about one step of the run (primitives only, so the rules are testable)."""
    name: str                  # screen | triage | plan | implement | escalate
    attempted: bool            # a model call was made for it, or it left a valid artifact
    artifact_exists: bool
    artifact_fresh: bool
    step_failed: bool          # its latest real dispatch failed and nothing recovered it
    review: str                # pass | revise | escalate | none | stale
    test_failed: bool = False  # implement only: the plan's test did not pass
    flagged: bool = False      # screen only: the verdict is not clean
    evidence: tuple[str, ...] = ()  # screen only: the quotes behind a flagged verdict


@dataclass(frozen=True)
class Label:
    text: str
    detail: str | None = None

    def __str__(self):
        return f"{self.text} ({self.detail})" if self.detail else self.text


def _by_review(review):
    if review == REVIEW_PASS:
        return Label(PASS)
    if review == REVIEW_NONE:
        return Label(UNREVIEWED)
    if review == REVIEW_STALE:
        return Label(STALE, "review outdated")
    return Label(FAIL, review)


def label_of(facts):
    if not facts.attempted:
        return Label(NOT_RUN)
    if facts.step_failed or not facts.artifact_exists:
        return Label(FAIL, "step failed")
    if not facts.artifact_fresh:
        return Label(STALE, "artifact outdated")
    if facts.name == "screen" and facts.flagged:
        return Label(FLAGGED, f"review: {facts.review}")
    if facts.test_failed:
        return Label(FAIL, "test failed")
    if facts.name == "escalate":
        return Label(AWAITING_HUMAN, f"review: {facts.review}")
    return _by_review(facts.review)


def heading_of(section, labels):
    """`## Implement: FAIL (revise)` for labelled sections, `## Overview` otherwise."""
    agent = LABELLED_SECTIONS.get(section)
    title = SECTION_TITLES[section]
    return f"## {title}: {labels[agent]}" if agent else f"## {title}"


def status_lines(labels):
    """The deterministic review summary: one line per step, naming missing, failed or stale reviews."""
    return [f"- {SECTION_TITLES[section]}: {labels[agent]}" for section, agent in LABELLED_SECTIONS.items()
            if not (agent in OPTIONAL_STEPS and labels[agent].text == NOT_RUN)]


def evidence_block(evidence):
    """The screen's quoted report text as a fenced `text` block, one item per line; "" when there is none.

    The fence is one backtick longer than any run inside an item, so attacker text can neither
    close it nor render as Markdown (headings, links, HTML).
    """
    if not evidence:
        return ""
    longest = max((len(run) for item in evidence for run in _BACKTICK_RUN.findall(item)), default=0)
    fence = "`" * max(MIN_FENCE, longest + 1)
    return "\n".join([EVIDENCE_INTRO, "", f"{fence}text", *evidence, fence])


def _top(result, human_required):
    lines = [f"{RESULT_PREFIX}{result}", ""]
    return lines + ([HUMAN_BANNER, ""] if human_required else [])


def _section_head(section, labels, screen_evidence):
    """The code-made heading and, for the screen, the quoted evidence directly under its label."""
    lines = [heading_of(section, labels), ""]
    block = evidence_block(screen_evidence) if section == "screen" else ""
    return lines + ([block, ""] if block else [])


def assemble_draft(terminal_state, human_required, sections, labels, screen_evidence=()):
    """The writer draft: result line, banner, then each present section under its code-made heading."""
    lines = _top(terminal_state, human_required)
    for section in (s for s in SECTION_ORDER if s in sections):
        lines += _section_head(section, labels, screen_evidence) + [sections[section].strip(), ""]
        if section == "review":
            lines += ["Review status of each step, written by the harness:", "", *status_lines(labels), ""]
    return "\n".join(lines)


def _unfenced_lines(text):
    """The lines outside fenced code blocks (a closing fence needs at least as many backticks as the opening)."""
    outside, fence_length = [], 0
    for line in text.splitlines():
        opening = _FENCE_OPEN.match(line)
        if fence_length:
            if opening and len(opening.group(1)) >= fence_length and not line.strip("`"):
                fence_length = 0
        elif opening:
            fence_length = len(opening.group(1))
        else:
            outside.append(line)
    return outside


def section_heading_count(draft):
    """Section headings in the draft; text inside a fenced block (the quoted evidence) is not a heading."""
    return sum(1 for line in _unfenced_lines(draft) if _SECTION_HEADING.match(line))


def all_heading_lines(text):
    return [line for line in _unfenced_lines(text) if _HEADING.match(line)]


def result_of(draft):
    """The terminal state on a draft's top line, or None."""
    found = _RESULT_LINE.search(draft)
    return found.group(1) if found else None


def render_failure(result, human_required, human_reasons, labels, failures, drafts, screen_evidence=()):
    """The deterministic FAILED_<reason> report. No model wrote any of it."""
    lines = _top(result, human_required)
    lines += ["This report was written by code from the audit log, with no model call. No accepted writer "
              "artifact exists: " + (f"{len(drafts)} writer draft(s) were made but none passed review of its "
                                      "exact text." if drafts else "no writer draft was made."), ""]
    for section in LABELLED_SECTIONS:
        lines += _section_head(section, labels, screen_evidence)
    lines += ["## What failed", ""]
    lines += [f"- step {f['step']} {f['agent']}: {f['error_code']}: {f['detail']}" for f in failures] or ["- no dispatch failed"]
    lines += ["", "## Why a human is required", ""]
    lines += [f"- {reason}" for reason in human_reasons] or ["- no human decision is required by the run"]
    lines += ["", "## Writer drafts (not accepted)", ""]
    lines += [f"- {name}" for name in drafts] or ["- none"]
    return "\n".join(lines) + "\n"
