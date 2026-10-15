"""The one place a Coordinator is assembled, over the same dispatcher wiring the evals use.

Everything that varies (LLM client, directories, sandbox, clock, log) is a parameter, so tests
inject a fake LLM and temp directories and exercise the real parts.
"""
from harness.audit import AuditLog
from harness.coordinator import MAX_TURNS, Coordinator
from harness.freshness import Freshness
from harness.launch_router import LaunchRouter
from harness.manifest import load_coordinator_spec
from harness.pricing import PriceTable
from harness.prompts import PromptLibrary
from harness.run_state import RunState
from harness.store import ArtifactStore
from harness.terminal_state import TerminalStateCheck
from harness.wiring import build_dispatcher
from harness.writer_stub import WriterStub


def build_coordinator(*, llm, run_dir, tool_root, sandbox, bug_report_path, correlation_id, clock, log=None,
                      max_turns=MAX_TURNS):
    store = ArtifactStore(run_dir)
    audit = AuditLog(run_dir / "audit.jsonl")  # the same file the dispatcher appends to
    freshness = Freshness(store, sandbox)
    state = RunState(store, audit, freshness)
    terminal_check = TerminalStateCheck(store, freshness, state)
    writer = WriterStub(terminal_check, audit, correlation_id, clock)
    dispatcher = build_dispatcher(llm=llm, run_dir=run_dir, tool_root=tool_root, sandbox=sandbox,
                                  bug_report_path=bug_report_path, correlation_id=correlation_id, clock=clock)
    return Coordinator(
        llm=llm, spec=load_coordinator_spec(), system_prompt=PromptLibrary().system_prompt("coordinator"),
        router=LaunchRouter(dispatcher, writer), run_state=state, writer=writer, terminal_check=terminal_check,
        audit=audit, prices=PriceTable.from_file(), correlation_id=correlation_id, clock=clock, log=log,
        max_turns=max_turns,
    )
