"""Writer checks 2 to 5 of plan Section 6 (check 1, the schema, is the ordinary SchemaCheck).

Each check is a pure function over the sections (so evals reuse the exact rule) plus a small
object with run(ctx, artifact) -> Failure | None for the dispatcher's check list. All of them
fail with SCHEMA_INVALID, the usual structured error for an artifact that breaks its contract.
"""
from harness.report import section_heading_count
from harness.results import ErrorCode, Failure


def empty_sections(sections):
    """Keys whose value has no visible text."""
    return sorted(key for key, text in sections.items() if not text.strip())


def sections_with_heading_lines(sections):
    """Keys with a prose line starting with '#': the model must not write headings."""
    return sorted(key for key, text in sections.items()
                  if any(line.lstrip().startswith("#") for line in text.splitlines()))


def section_set_problem(sections, required):
    missing, extra = sorted(set(required) - set(sections)), sorted(set(sections) - set(required))
    problems = ([f"missing section(s): {', '.join(missing)}"] if missing else []) + \
               ([f"unexpected section(s): {', '.join(extra)}"] if extra else [])
    return "; ".join(problems) or None


def draft_problem(draft, section_count):
    """Check 5: the assembled draft is non-empty and has exactly one heading per section."""
    if not draft.strip():
        return "the assembled draft is empty"
    found = section_heading_count(draft)
    return None if found == section_count else f"the draft has {found} section heading(s) for {section_count} section(s)"


def _invalid(detail):
    return Failure(ErrorCode.SCHEMA_INVALID, detail)


class NonEmptySections:
    def run(self, ctx, artifact):
        empty = empty_sections(artifact["sections"])
        return _invalid(f"empty section(s): {', '.join(empty)}") if empty else None


class NoHeadingLines:
    def run(self, ctx, artifact):
        bad = sections_with_heading_lines(artifact["sections"])
        return _invalid(f"section(s) with a line starting with '#' (headings are written by code): {', '.join(bad)}") if bad else None


class ExactSectionSet:
    def run(self, ctx, artifact):
        problem = section_set_problem(artifact["sections"], ctx.report.required_sections())
        return _invalid(f"{problem}; required: {', '.join(ctx.report.required_sections())}") if problem else None


class AssembledDraft:
    def run(self, ctx, artifact):
        sections = artifact["sections"]
        problem = draft_problem(ctx.report.draft(ctx.terminal_state, sections), len(sections))
        return _invalid(problem) if problem else None
