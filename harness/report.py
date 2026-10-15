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
    "overview": "Overview", "triage": "Triage", "plan": "Plan", "implement": "Implement",
    "escalation": "Escalation", "review": "Review",
}
SECTION_ORDER = tuple(SECTION_TITLES)
LABELLED_SECTIONS = {"triage": "triage", "plan": "plan", "implement": "implement", "escalation": "escalate"}
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_SECTION_HEADING = re.compile(r"^##\s", re.MULTILINE)
_RESULT_LINE = re.compile(rf"^{re.escape(RESULT_PREFIX)}(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class StepFacts:
    """What code knows about one step of the run (primitives only, so the rules are testable)."""
    name: str                  # triage | plan | implement | escalate
    attempted: bool            # a model call was made for it, or it left a valid artifact
    artifact_exists: bool
    artifact_fresh: bool
    step_failed: bool          # its latest real dispatch failed and nothing recovered it
    review: str                # pass | revise | escalate | none | stale
    test_failed: bool = False  # implement only: the plan's test did not pass


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
            if not (agent == "escalate" and labels[agent].text == NOT_RUN)]  # escalation is off the normal path


def _top(result, human_required):
    lines = [f"{RESULT_PREFIX}{result}", ""]
    return lines + ([HUMAN_BANNER, ""] if human_required else [])


def assemble_draft(terminal_state, human_required, sections, labels):
    """The writer draft: result line, banner, then each present section under its code-made heading."""
    lines = _top(terminal_state, human_required)
    for section in (s for s in SECTION_ORDER if s in sections):
        lines += [heading_of(section, labels), "", sections[section].strip(), ""]
        if section == "review":
            lines += ["Review status of each step, written by the harness:", "", *status_lines(labels), ""]
    return "\n".join(lines)


def section_heading_count(draft):
    return len(_SECTION_HEADING.findall(draft))


def all_heading_lines(text):
    return [line for line in text.splitlines() if _HEADING.match(line)]


def result_of(draft):
    """The terminal state on a draft's top line, or None."""
    found = _RESULT_LINE.search(draft)
    return found.group(1) if found else None


def render_failure(result, human_required, human_reasons, labels, failures, drafts):
    """The deterministic FAILED_<reason> report. No model wrote any of it."""
    lines = _top(result, human_required)
    lines += ["This report was written by code from the audit log, with no model call. No accepted writer "
              "artifact exists: " + (f"{len(drafts)} writer draft(s) were made but none passed review of its "
                                      "exact text." if drafts else "no writer draft was made."), ""]
    for section, agent in LABELLED_SECTIONS.items():
        lines += [heading_of(section, labels), ""]
    lines += ["## What failed", ""]
    lines += [f"- step {f['step']} {f['agent']}: {f['error_code']}: {f['detail']}" for f in failures] or ["- no dispatch failed"]
    lines += ["", "## Why a human is required", ""]
    lines += [f"- {reason}" for reason in human_reasons] or ["- no human decision is required by the run"]
    lines += ["", "## Writer drafts (not accepted)", ""]
    lines += [f"- {name}" for name in drafts] or ["- none"]
    return "\n".join(lines) + "\n"
