import asyncio
import json

import pytest

from agent.usage_meter import metered_model
from scripts.generate_independent_validation import generate
from scripts.evaluate_independent import (verify_freeze, run_case, aggregate,
    apply_test_confirmations, oracle_diagnosis)


def test_freeze_declares_sources_splits_and_detects_tampering(tmp_path):
    folder = tmp_path / "v1"
    generate(folder)
    manifest = verify_freeze(folder)
    assert len(manifest["cases"]) == 24
    assert sum(c["split"] == "holdout" for c in manifest["cases"]) == 16
    assert manifest["source"] == "synthetic_only"
    assert all("truth.json" in p or "confirmation.json" in p or p.endswith((".csv", ".xlsx", ".pdf"))
               for p in manifest["files"])
    with pytest.raises(ValueError, match="禁止覆盖"):
        generate(folder)
    case = folder / manifest["cases"][0]["id"]
    (case / "truth.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="冻结样本"):
        verify_freeze(folder)


def test_model_arm_never_reads_oracle_mapping_and_skipped_model_is_not_success(tmp_path, monkeypatch):
    import scripts.evaluate_independent as module
    folder = tmp_path / "v1"
    generate(folder)
    manifest = verify_freeze(folder)
    item = next(c for c in manifest["cases"] if c["family"] == "unfamiliar-en")
    case = folder / item["id"]
    # A model returning no candidates must not silently receive the answer.
    def empty_model(run, *args):
        return {"revision": module.revision_key(run), "mode": "model_tools", "summary": "test fixture",
                "proposals": [], "model_requests": [], "log": []}
    monkeypatch.setattr(module, "diagnose", empty_model)
    (case / "confirmation.json").write_text("not valid JSON", encoding="utf-8")
    record, report = run_case(case, "agent_test_confirmation", ("deepseek", "test-fixture", "test-fixture"))
    assert not record["success"] and record["simulated_confirmations"] == 0
    assert record["real_human_seconds"] is None
    assert record["anomaly"]["fn"] == 2 and report
    missing, _ = run_case(case, "agent_test_confirmation")
    assert missing["error"] and not missing["success"]


def test_normal_and_correct_mapping_are_scored_independently(tmp_path):
    folder = tmp_path / "v1"
    generate(folder)
    manifest = verify_freeze(folder)
    selected = [manifest["cases"][0], next(c for c in manifest["cases"] if c["family"] == "unfamiliar-en")]
    before, after = [], []
    for case in selected:
        row, _ = run_case(folder / case["id"], "baseline")
        before.append(row)
        row, _ = run_case(folder / case["id"], "oracle_test_confirmation")
        after.append(row)
    assert before[0]["success"] and not before[1]["success"]
    assert all(row["success"] for row in after)
    summary = aggregate(after)
    assert summary["successful_cases"] == 2 and summary["simulated_confirmations"] == 1
    assert summary["real_human_confirmations"] == 0 and summary["actual_billed_cost"] is None


def test_meter_records_returned_usage_and_preserves_unknown_failed_usage():
    from agents import Model
    from agents.items import ModelResponse
    from agents.usage import Usage

    class ModelFixture(Model):
        async def get_response(self, *args, **kwargs):
            if kwargs.get("fail"):
                raise RuntimeError("private response text")
            return ModelResponse(output=[], usage=Usage(requests=1, input_tokens=13, output_tokens=7), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    events = []
    model = metered_model(ModelFixture(), events)
    asyncio.run(model.get_response())
    with pytest.raises(RuntimeError):
        asyncio.run(model.get_response(fail=True))
    assert len(events) == 2 and events[0]["input_tokens"] == 13 and events[0]["output_tokens"] == 7
    assert events[1]["input_tokens"] is None and events[1]["status"] == "error"
    assert "private response text" not in json.dumps(events)


def test_published_evaluation_ui_exposes_limits_failures_and_downloads():
    from streamlit.testing.v1 import AppTest
    from evaluation_ui import RESULTS, DATASET
    if not (RESULTS / "comparison.json").exists():
        pytest.skip("Published experiment artifacts are not generated yet")
    original = (RESULTS / "comparison.json").read_bytes()
    app = AppTest.from_string("from evaluation_ui import evaluation_panel\nevaluation_panel()").run(timeout=30)
    assert not app.exception
    assert any("程序模拟确认" in message.value for message in app.info)
    assert len(app.get("download_button")) == 3
    assert len(app.selectbox) == 3
    # Switching groups is read-only and does not issue a model request.
    next(s for s in app.selectbox if s.label == "查看处理方式").set_value("agent_test_confirmation").run()
    assert not app.exception
    assert (RESULTS / "comparison.json").read_bytes() == original


def test_replay_actual_bad_model_mapping_preserves_duplicate_sku():
    from agent.tools import ReviewRun, run_rules
    from agent.diagnostic_agent import Context, inspect_candidate_tables, apply_proposal, revision_key
    from core.demo import DATA, load_case
    from core.parser import ParseError
    results = json.loads((DATA / "independent_results/v1-live/comparison.json").read_text(encoding="utf-8"))
    failed = next(row for row in results["arms"]["agent_test_confirmation"]["records"]
                  if row["case"] == "21-manual-boundaries-duplicate")
    # Observed failure, now a regression case; never labelled unseen testing again.
    proposal = failed["confirmations"][0]["proposal"]
    run = ReviewRun(load_case(DATA / "independent_validation/v1" / failed["case"]), "zh", "rules")
    run_rules(run)
    context = Context(run)
    inspect_candidate_tables(context)
    assert all(table.anchor.row < 2 for kind, table in context.tables.values())
    original = [d.model_dump_json() for d in run.documents]
    diagnosis = {"revision": revision_key(run), "proposals": [proposal]}
    with pytest.raises(ParseError, match="商品行作为表头"):
        apply_proposal(run, diagnosis, 0)
    assert original == [d.model_dump_json() for d in run.documents]
    assert any(f.rule == "SKU_DUPLICATE" for f in run.result.findings)


def test_table_confirmation_rejects_loss_of_existing_row_positions(monkeypatch):
    from agent import tools
    from core.demo import DATA, load_case
    from core.parser import TableAnchor, ParseError
    run = tools.ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
    tools.run_rules(run)
    invoice = next(d for d in run.documents if d.kind == "CI")
    corrupted = invoice.model_copy(deep=True)
    corrupted.items[0].anchor.row = 999
    monkeypatch.setattr(tools, "parse_document", lambda *args: corrupted)
    with pytest.raises(ParseError, match="丢失已有商品行"):
        tools.confirm_table_mapping(run, "CI", TableAnchor(1), {1: "sku", 2: "quantity"})
    assert invoice.items[0].anchor.row != 999
