"""The evidence script's aggregation logic, on small hand-made promptfoo result files. No model, no network."""
import csv
import json
import sys

from harness.tools import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_evidence as evidence  # noqa: E402


def line(step, agent="triage", model="m-small", i=100, o=50, cost=0.001, cid="eval", ts="t1"):
    return {"correlation_id": cid, "step": step, "agent": agent, "model": model, "input_tokens": i,
            "output_tokens": o, "cost_usd": cost, "ts": ts}


def result(case, success, metrics, audit=None, output=None):
    components = [{"assertion": {"type": "is-json"}, "pass": True}]
    components += [{"assertion": {"type": "python", "metric": m}, "pass": ok} for m, ok in metrics.items()]
    return {"testCase": {"description": case}, "success": success, "gradingResult": {"componentResults": components},
            "response": {"output": json.dumps(output) if output else "{}", "metadata": {"audit": audit} if audit else {}}}


def write_results(path, results):
    path.write_text(json.dumps({"results": {"results": results}}), encoding="utf-8")


def test_per_case_and_per_criterion_pass_rates(tmp_path):
    path = tmp_path / "triage.json"
    write_results(path, [result("a", True, {"x": True, "y": True}), result("a", False, {"x": True, "y": False}),
                         result("b", True, {"x": True})])
    rows = {(r["case"], r["criterion"]): r for r in evidence.success_rows([path])}
    assert (rows["a", evidence.ALL_ASSERTIONS]["passed"], rows["a", evidence.ALL_ASSERTIONS]["total"]) == (1, 2)
    assert rows["a", evidence.ALL_ASSERTIONS]["pass_rate"] == 0.5 and rows["b", evidence.ALL_ASSERTIONS]["pass_rate"] == 1.0
    assert (rows[evidence.ALL_CASES, "x"]["passed"], rows[evidence.ALL_CASES, "x"]["total"]) == (3, 3)
    assert (rows[evidence.ALL_CASES, "y"]["passed"], rows[evidence.ALL_CASES, "y"]["total"]) == (1, 2)
    assert {r["suite"] for r in rows.values()} == {"triage"}


def test_workflow_repeats_roll_up_into_one_case_with_the_observed_terminal_states(tmp_path):
    path = tmp_path / "workflow.json"
    states = ["VERIFIED", "VERIFIED", "FAILED_COORDINATION"]
    write_results(path, [result("bug-clear", s == "VERIFIED", {"terminal-state": s == "VERIFIED"}, output={"result": s})
                         for s in states])
    [case] = [r for r in evidence.success_rows([path]) if r["case"] == "bug-clear"]
    assert (case["passed"], case["total"]) == (2, 3) and case["pass_rate"] == 0.6667
    assert case["observed"] == "FAILED_COORDINATION x1, VERIFIED x2"


def test_unreadable_or_empty_result_files_are_skipped(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    write_results(tmp_path / "empty.json", [])
    assert evidence.success_rows([tmp_path / "bad.json", tmp_path / "empty.json", tmp_path / "nosuch.json"]) == []


def test_csv_has_the_documented_columns(tmp_path):
    write_results(tmp_path / "plan.json", [result("a", True, {"x": True})])
    out = tmp_path / "success_metrics.csv"
    evidence.write_csv(evidence.success_rows([tmp_path / "plan.json"]), out)
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0]) == evidence.CSV_COLUMNS and rows[0]["pass_rate"] == "1.0"


def test_audit_totals_add_tokens_and_calculated_cost_per_model_and_agent():
    summary = evidence.summarize_audit({"s": [line(1), line(2, agent="plan", model="m-big", i=1000, o=10, cost=0.01, ts="t2")]})
    assert summary["totals"] == {"audit_lines": 2, "input_tokens": 1100, "output_tokens": 60, "cost_usd": 0.011}
    assert summary["by_model"]["m-big"]["input_tokens"] == 1000 and summary["by_agent"]["triage"]["audit_lines"] == 1


def test_a_line_found_in_two_sources_is_counted_once():
    shared = line(1, cid="20261015-120000")
    summary = evidence.summarize_audit({"results/workflow.json": [shared], "runs/20261015-120000": [shared, line(2, cid="20261015-120000", ts="t2")]})
    assert summary["totals"]["audit_lines"] == 2
    assert summary["sources"]["runs/20261015-120000"]["duplicate_lines_skipped"] == 1


def test_sources_come_from_result_files_then_run_directories(tmp_path):
    write_results(tmp_path / "plan.json", [result("a", True, {}, audit=[line(1)]), result("b", True, {})])
    run = tmp_path / "runs" / "20261015-1"
    run.mkdir(parents=True)
    run.joinpath("audit.jsonl").write_text(json.dumps(line(1, cid="20261015-1")) + "\n\n")
    summary = evidence.build_audit_summary([tmp_path / "plan.json"], [run])
    assert list(summary["sources"]) == ["results/plan.json", "runs/20261015-1"]
    assert summary["totals"]["audit_lines"] == 2
    assert summary["results_without_audit_lines"] == {"results/plan.json": 1}
    assert any("judge" in note for note in summary["notes"])      # what is not counted is stated in the file itself


def test_a_run_directory_without_an_audit_file_adds_nothing(tmp_path):
    assert evidence.audit_lines_from_run(tmp_path) == []


def test_promptfoo_turning_a_float_zero_into_an_integer_does_not_defeat_the_deduplication():
    original = line(1, cost=0.0, cid="20261015-1")
    normalised = {**original, "cost_usd": 0}
    summary = evidence.summarize_audit({"results/workflow.json": [normalised], "runs/20261015-1": [original]})
    assert summary["totals"]["audit_lines"] == 1
