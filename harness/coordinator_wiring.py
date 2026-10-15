"""The one place a Coordinator is assembled, over the same dispatcher wiring the evals use.

Everything that varies (LLM client, directories, sandbox, clock, log) is a parameter, so tests
inject a fake LLM and temp directories and exercise the real parts.
"""
from harness.coordinator import MAX_TURNS, Coordinator
from harness.launch_router import LaunchRouter
from harness.manifest import load_coordinator_spec
from harness.pricing import PriceTable
from harness.prompts import PromptLibrary
from harness.report_publisher import ReportPublisher
from harness.run_views import RunViews
from harness.wiring import build_dispatcher


def build_coordinator(*, llm, run_dir, tool_root, sandbox, bug_report_path, correlation_id, clock, log=None,
                      max_turns=MAX_TURNS):
    views = RunViews.build(run_dir, sandbox)  # shared with the dispatcher: one audit.jsonl, one store
    dispatcher = build_dispatcher(llm=llm, run_dir=run_dir, tool_root=tool_root, sandbox=sandbox,
                                  bug_report_path=bug_report_path, correlation_id=correlation_id, clock=clock,
                                  views=views)
    return Coordinator(
        llm=llm, spec=load_coordinator_spec(), system_prompt=PromptLibrary().system_prompt("coordinator"),
        router=LaunchRouter(dispatcher), run_state=views.state, publisher=ReportPublisher(run_dir, views),
        terminal_check=views.terminal_check, audit=views.audit, prices=PriceTable.from_file(),
        correlation_id=correlation_id, clock=clock, log=log, max_turns=max_turns,
    )
