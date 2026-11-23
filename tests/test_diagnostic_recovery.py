import asyncio
import json
from types import SimpleNamespace

import pytest

from agent import diagnostic_agent as module
from agent.diagnostic_output import output_schema, partial_output, constrain_repair
from agent.tools import ReviewRun, run_rules
from core.demo import DATA, load_case
from core.diagnostic_presenter import proposal_card, preview_impact

TYPES = {"mappings": module.MappingSuggestion, "fields": module.FieldSuggestion, "links": module.LinkSuggestion}
FIELD = {"source": "CI:source:0", "kind": "CI", "field": "order_id", "value": "PO-123"}


def run_case():
    run = ReviewRun(load_case(DATA / "independent_validation/v1/18-metadata-quantity"), "zh", "rules")
    run_rules(run)
    return run


def test_schema_reports_paths_without_raw_private_values():
    from agents import ModelBehaviorError
    state = {}
    with pytest.raises(ModelBehaviorError):
        output_schema(module.DiagnosticOutput, state).validate_json(json.dumps({"summary": "PRIVATE-DATA", "fields": [FIELD]}))
    assert state["valid"] is False and state["issues"]
    assert "PRIVATE-DATA" not in json.dumps(state["issues"])


def test_salvage_preserves_pairs_and_never_invents_missing_values():
    bad = json.dumps({"mappings": [{"table": "CI:table:0", "columns": [{"column": 4, "field": "quantity"}]}],
                      "fields": [FIELD, {**FIELD, "kind": "invented"}], "links": []})
    recovered = partial_output(bad, module.DiagnosticOutput, TYPES)
    assert recovered.fields == [module.FieldSuggestion(**FIELD)]
    assert recovered.mappings[0].columns.proposals[0].column == 4
    assert partial_output("broken JSON", module.DiagnosticOutput, TYPES) is None
    assert partial_output('{"fields":[{"value":"123"}]}', module.DiagnosticOutput, TYPES) is None
    assert constrain_repair(recovered, recovered).fields == recovered.fields
    changed = recovered.model_copy(deep=True)
    changed.fields[0].value = "PO-999"
    with pytest.raises(ValueError, match="改变"):
        constrain_repair(changed, recovered)


def test_exactly_one_fenced_json_is_unwrapped_and_still_strictly_validated():
    from agents import ModelBehaviorError
    valid = json.dumps({"summary": "candidate", "mappings": [], "fields": [FIELD], "links": []})
    text = "诊断结果\n```json\n" + valid + "\n```\n请人工确认。"
    state = {}
    result = output_schema(module.DiagnosticOutput, state).validate_json(text)
    assert result.fields[0].value == "PO-123" and state["unwrapped"] and state["valid"]
    for ambiguous in [text + "\n```json\n" + valid + "\n```", "```json\n{broken}\n```", valid + valid]:
        with pytest.raises(ModelBehaviorError):
            output_schema(module.DiagnosticOutput, {}).validate_json(ambiguous)


def test_fenced_output_does_not_issue_an_additional_model_request(monkeypatch):
    from agents import Runner
    calls = []
    async def fixture(agent, *args, **kwargs):
        calls.append(1)
        text = json.dumps({"summary": "candidate", "mappings": [], "fields": [], "links": []})
        return SimpleNamespace(final_output=agent.output_type.validate_json("```json\n" + text + "\n```"))
    monkeypatch.setattr(Runner, "run", fixture)
    context = module.Context(run_case())
    asyncio.run(module._diagnose(context, "fixture", "fixture", "openai"))
    assert len(calls) == 1 and context.completion_mode == "unwrapped_output"
    assert context.output_events[0]["additional_model_requests"] == 0


@pytest.mark.parametrize("repair", ["valid", "invalid", "invented", "api_failure"])
def test_one_bounded_format_retry_and_safe_partial_results(monkeypatch, repair):
    from agents import Runner
    calls = []
    original = {"summary": "candidate", "fields": [FIELD], "links": []}  # missing mappings
    valid = {**original, "mappings": []}

    async def fixture(agent, prompt, **kwargs):
        calls.append(agent)
        if len(calls) == 1:
            agent.output_type.validate_json(json.dumps(original))
        if repair == "api_failure":
            raise RuntimeError("private transport detail")
        value = original if repair == "invalid" else valid
        if repair == "invented":
            value = {**valid, "fields": [{**FIELD, "value": "PO-999"}]}
        return SimpleNamespace(final_output=agent.output_type.validate_json(json.dumps(value)))

    monkeypatch.setattr(Runner, "run", fixture)
    context = module.Context(run_case())
    output = asyncio.run(module._diagnose(context, "fixture-not-real", "fixture", "openai"))
    assert len(calls) == 2 and calls[1].tools == []
    assert output.fields[0].value == FIELD["value"]
    assert context.completion_mode == ("repaired_output" if repair == "valid" else "partial_output")
    assert "private transport detail" not in json.dumps(context.output_events)
    assert "previous_output" not in json.dumps(context.output_events)


def test_non_output_failures_are_not_retried(monkeypatch):
    from agents import Runner, ModelBehaviorError
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise ModelBehaviorError("invalid tool call")
    monkeypatch.setattr(Runner, "run", fail)
    with pytest.raises(ModelBehaviorError):
        asyncio.run(module._diagnose(module.Context(run_case()), "fixture", "fixture", "openai"))
    assert len(calls) == 1


def test_card_and_preview_show_source_change_and_keep_original_immutable():
    run = run_case()
    context = module.Context(run)
    module.inspect_missing_fields(context)
    ref = next(k for k, v in context.snippets.items() if "NX-7818" in v[2])
    output = module.DiagnosticOutput(summary="candidate", mappings=[], fields=[module.FieldSuggestion(
        source=ref, kind="CI", field="order_id", value="NX-7818")], links=[])
    diagnosis = {"revision": module.revision_key(run), "proposals": module.validated(context, output)}
    before = module.revision_key(run)
    card = proposal_card(run, diagnosis["proposals"][0])
    assert "NX-7818" in card["quote"] and "CI.csv" in card["source"]
    assert card["rows"][0]["当前值"] == "未识别"
    impact = preview_impact(run, diagnosis, 0)
    assert impact[2]["预览修改后"] < impact[2]["修改前"]
    assert before == module.revision_key(run)


def test_confirmation_ui_previews_without_applying(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import diagnostic_ui
    run = run_case()
    context = module.Context(run)
    module.inspect_missing_fields(context)
    output = module.DiagnosticOutput(summary="待核对", mappings=[], fields=[module.FieldSuggestion(
        source=next(iter(context.snippets)), kind="CI", field="order_id", value="NX-7818")], links=[])
    diagnosis = {"revision": module.revision_key(run), "proposals": module.validated(context, output),
                 "summary": "待核对", "log": [], "mode": "partial_output"}
    monkeypatch.setattr(diagnostic_ui, "_fixture", run, raising=False)
    app = AppTest.from_string("import streamlit as st\nimport diagnostic_ui\n"
        "diagnostic_ui.diagnostic_panel(diagnostic_ui._fixture,'','','openai')")
    app.session_state["diagnostics"] = diagnosis
    app.run(timeout=30)
    assert not app.exception and app.warning
    next(b for b in app.button if b.label == "预览复审结果").click().run(timeout=30)
    assert not app.exception and len(app.dataframe) >= 2
    assert next(b for b in app.button if b.label == "应用此建议并复审").disabled
    assert "order_id" not in next(d for d in run.documents if d.kind == "CI").header
