"""The one place a Dispatcher is assembled from its parts.

Real runs, evals and tests all call build_dispatcher, so they exercise the same wiring.
Everything that varies (LLM client, directories, sandbox, clock) is a parameter.
"""
from harness.agents import PROFILES
from harness.audit import AuditLog
from harness.dispatcher import Dispatcher
from harness.freshness import Freshness
from harness.manifest import load_agent_specs
from harness.pricing import PriceTable
from harness.prompts import PromptLibrary
from harness.rejected import RejectedWriter
from harness.store import ArtifactStore
from harness.tool_loop import ToolLoop
from harness.tool_runner import ToolRunner


def build_dispatcher(*, llm, run_dir, tool_root, sandbox, bug_report_path, correlation_id, clock):
    """A Dispatcher whose artifacts, audit log and rejected/ files live under run_dir."""
    store = ArtifactStore(run_dir)
    return Dispatcher(
        specs=load_agent_specs(), profiles=PROFILES,
        tool_loop=ToolLoop(llm, ToolRunner(root=tool_root)),
        prompts=PromptLibrary(), prices=PriceTable.from_file(),
        store=store, freshness=Freshness(store, sandbox), sandbox=sandbox,
        audit=AuditLog(run_dir / "audit.jsonl"), rejected=RejectedWriter(run_dir),
        bug_report_path=bug_report_path, correlation_id=correlation_id, clock=clock,
    )
