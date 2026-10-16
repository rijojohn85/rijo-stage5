"""Per-agent dispatch rules, as data: preconditions, checks, artifact chain, one-line summary.

Adding an agent or a check means editing this table, not the dispatcher.
"""
from dataclasses import dataclass

from harness.checks import EscalationBackstop, PlanScopeCheck, ReviewBindingCheck, SchemaCheck
from harness.preconditions import (FreshPassingReviewOf, NoHumanCheckpoint, ReviewableTarget, TerminalStateIsLegal,
                                   TriageIsFixNow, WithinRevisionAllowance)
from harness.store import REVIEWER, WRITER
from harness.writer_checks import AssembledDraft, ExactSectionSet, NoHeadingLines, NonEmptySections

# Artifacts an agent's work builds on, ending with its own. Producers are shown everything
# before their own entry; the reviewer is shown the whole chain of its target.
ARTIFACT_CHAIN = {
    "screen": ("screen",),
    "triage": ("triage",),
    "plan": ("triage", "plan"),
    "implement": ("triage", "plan", "implement"),
    "escalate": ("triage", "escalate"),
    "writer": ("triage", "plan", "implement", "escalate", "writer"),
}


@dataclass(frozen=True)
class AgentProfile:
    preconditions: tuple
    checks: tuple
    summarize: callable
    shows_audit_failures: bool = False  # escalation can work from earlier errors


def _count(items):
    return f"{len(items)} finding(s)"


PROFILES = {
    "screen": AgentProfile(
        preconditions=(WithinRevisionAllowance(),),
        checks=(SchemaCheck("screen"),),
        summarize=lambda a: a["verdict"],  # the verdict only: evidence is quoted report text
    ),
    "triage": AgentProfile(
        preconditions=(WithinRevisionAllowance(),),
        checks=(SchemaCheck("triage"), EscalationBackstop()),
        summarize=lambda a: f"{a['classification']}, {a['decision']}, escalation={a['escalation']}",
    ),
    "plan": AgentProfile(
        preconditions=(TriageIsFixNow(), FreshPassingReviewOf("triage"), WithinRevisionAllowance(),
                       NoHumanCheckpoint()),
        checks=(SchemaCheck("plan"),),
        summarize=lambda a: f"{len(a['files'])} file(s); test: {a['test']}",
    ),
    "implement": AgentProfile(
        preconditions=(FreshPassingReviewOf("plan"), WithinRevisionAllowance(), NoHumanCheckpoint()),
        checks=(SchemaCheck("implement"), PlanScopeCheck()),
        summarize=lambda a: a["summary"],
    ),
    REVIEWER: AgentProfile(
        preconditions=(ReviewableTarget(),),
        checks=(SchemaCheck("review"), ReviewBindingCheck()),
        summarize=lambda a: f"{a['verdict']}, {_count(a['findings'])}",
    ),
    "escalate": AgentProfile(
        preconditions=(WithinRevisionAllowance(),),
        checks=(SchemaCheck("escalation"),),
        summarize=lambda a: a["reason"],
        shows_audit_failures=True,
    ),
    WRITER: AgentProfile(
        preconditions=(TerminalStateIsLegal(), WithinRevisionAllowance()),
        checks=(SchemaCheck("writer"), NonEmptySections(), NoHeadingLines(), ExactSectionSet(), AssembledDraft()),
        summarize=lambda a: f"{len(a['sections'])} section(s)",
    ),
}
