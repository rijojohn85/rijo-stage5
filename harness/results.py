"""Error codes and the only two result shapes a dispatch can return to the coordinator."""
from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_JSON = "NOT_JSON"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PLAN_SCOPE_VIOLATION = "PLAN_SCOPE_VIOLATION"
    REVIEW_BINDING_MISMATCH = "REVIEW_BINDING_MISMATCH"
    PRECONDITION_MISSING = "PRECONDITION_MISSING"
    REVISION_CAP_REACHED = "REVISION_CAP_REACHED"
    API_ERROR = "API_ERROR"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    EXTRA_TOOL_CALL = "EXTRA_TOOL_CALL"


@dataclass(frozen=True)
class Failure:
    """Why a dispatch was rejected. raw_output is kept for rejected/, never returned."""
    code: ErrorCode
    detail: str
    raw_output: str | None = None


def ok_result(agent, summary, artifact):
    return {"ok": True, "agent": agent, "summary": summary, "artifact": artifact}


def error_result(agent, failure):
    """The coordinator sees only the code and detail, never the invalid output itself."""
    return {"ok": False, "agent": agent, "error_code": str(failure.code), "detail": failure.detail}
