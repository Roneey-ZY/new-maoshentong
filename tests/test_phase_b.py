import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from agent.tools import InputFile, ReviewRun, run_rules, revise_preview
from agent.diagnostic_agent import (Context, DiagnosticOutput, FieldSuggestion, LinkSuggestion,
    MappingSuggestion, applicable, apply_proposal, diagnose, inspect_candidate_tables,
    inspect_item_links, inspect_missing_fields, make_diagnostic_agent, preflight,
    revision_key, validated)
from agent.mapping_agent import Proposal, ProposalList
from core.authentication import make_config
from core.demo import DATA, load_case
from core.review import ReviewBook
from core.storage import Store, AccessDenied, Conflict
from core.task_snapshot import pack, unpack


PASSWORD = "local-test-password-only"


@pytest.fixture
def store(tmp_path):
    instance = Store("sqlite:///" + str(tmp_path / "private.sqlite3"), allow_local=True)
    instance.initialize(make_config("admin", PASSWORD))
    return instance


def actors(store):
    admin = store.authenticate("admin", PASSWORD)["id"]
    a = store.create_user(admin, "reviewer-a", PASSWORD)
    b = store.create_user(admin, "reviewer-b", PASSWORD)
    return admin, a, b


def run_case(case="B"):
    run = ReviewRun(load_case(DATA / "demo" / case), "zh", "rules")
    run_rules(run)
    return run


def test_history_survives_reconnect_and_versions_are_immutable(store):
    admin, a, b = actors(store)
    run = run_case()
    first = pack((run, None, None, 0.7))
    task, version = store.save(a, "first", run.files, first, run.report)
    revised = revise_preview(run, "PL", 0, {"gross_kg": "130"})
    second = pack((revised, None, None, 0.5), baseline=run.result)
    assert store.save(a, "second", run.files, second, revised.report, task_id=task, expected_version=1) == (task, 2)
    reconnected = Store(store.dsn, allow_local=True)
    for number, expected in [(1, run), (2, revised)]:
        _, files, snapshot, report = reconnected.load(a, task, number)
        restored, _, baseline, _ = unpack(files, snapshot, report)
        assert restored[0].result == expected.result
        assert restored[0].files == expected.files
        assert report == expected.report
        assert bool(baseline) == (number == 2)
    assert len(store.versions(a, task)) == 2
    with pytest.raises(Conflict):
        store.save(a, "stale", run.files, first, run.report, task_id=task, expected_version=1)
    altered = [InputFile(f.kind, f.name, f.data + b"\n") for f in run.files]
    with pytest.raises(ValueError, match="原始文件"):
        store.save(a, "tamper", altered, first, run.report, task_id=task, expected_version=2)
    assert len(store.versions(a, task)) == 2


def test_every_task_entry_point_checks_owner_and_current_account(store):
    admin, a, b = actors(store)
    run = run_case()
    task, _ = store.save(a, "private", run.files, pack((run, None, None, 1)), run.report)
    assert store.list_tasks(b) == []
    assert len(store.list_tasks(admin)) == 1
    for intruder in [b, "forged-user"]:
        for action in [lambda: store.load(intruder, task, 1), lambda: store.versions(intruder, task),
                       lambda: store.save(intruder, "overwrite", run.files, {}, b"", task_id=task, expected_version=1),
                       lambda: store.create_user(intruder, "elevated", PASSWORD), lambda: store.users(intruder)]:
            with pytest.raises(AccessDenied):
                action()
    assert store.load(admin, task, 1)[3] == run.report
    with pytest.raises(AccessDenied):
        store.save(admin, "admin overwrite", run.files, {}, b"", task_id=task, expected_version=1)
    store.set_active(admin, a, False)
    assert store.user(a) is None
    assert store.authenticate("reviewer-a", PASSWORD) is None
    with pytest.raises(AccessDenied):
        store.list_tasks(a)
    with pytest.raises(ValueError):
        store.set_active(admin, admin, False)


def test_password_throttle_and_bootstrap_never_overwrite_users(store, monkeypatch):
    admin = store.authenticate("admin", PASSWORD)
    for _ in range(5):
        assert store.authenticate("admin", "wrong") is None
    assert store.authenticate("admin", PASSWORD) is None
    import core.storage as module
    now = module.time.time()
    monkeypatch.setattr(module.time, "time", lambda: now + 61)
    assert store.authenticate("admin", PASSWORD)["id"] == admin["id"]
    store.initialize(make_config("replacement", PASSWORD))
    assert store.authenticate("replacement", PASSWORD) is None


def test_snapshot_preserves_review_and_rejects_corruption(store):
    run = run_case()
    book = ReviewBook()
    book.record(run.result.findings[0], run.files, "explained", "partial_shipment", "客户确认分批发货", "test")
    diagnostics = {"summary": "not applied", "log": []}
    snapshot = pack((run, None, None, 1), book, run.result, diagnostics)
    files = [{"kind": f.kind, "name": f.name, "data": f.data, "sha256": hashlib.sha256(f.data).hexdigest()} for f in run.files]
    restored, review, baseline, diagnostic = unpack(files, snapshot, run.report)
    assert review.events == book.events and review.decisions == book.decisions
    assert diagnostic == diagnostics and baseline == run.result
    files[0]["data"] += b"tamper"
    with pytest.raises(ValueError, match="原文件"):
        unpack(files, snapshot, run.report)
    with pytest.raises(ValueError, match="先完成"):
        pack((ReviewRun(run.files, "zh", "rules"), None, None, 1))


def test_cloud_sqlite_is_rejected():
    with pytest.raises(ValueError, match="SQLite"):
        Store("sqlite:///cloud.sqlite3")
    with pytest.raises(ValueError):
        Store("https://invalid.example")


def test_reviewer_login_copied_name_recovery_and_revocation(store):
    admin, a, b = actors(store)
    # Creation strips names: pasting the same surrounding whitespace at login
    # must not turn an otherwise correct password into an authentication failure.
    uid = store.create_user(admin, "  测试审核员  ", PASSWORD)
    assert store.authenticate("  测试审核员  ", PASSWORD)["id"] == uid
    before = store.user(uid)["revision"]
    for _ in range(5):
        assert store.authenticate("测试审核员", "incorrect") is None
    assert store.authenticate("测试审核员", PASSWORD) is None
    store.unlock_reviewer(admin, uid)
    assert store.authenticate("测试审核员", PASSWORD)
    new_password = "different-test-password-2026"
    store.reset_reviewer_password(admin, uid, new_password)
    assert store.authenticate("测试审核员", PASSWORD) is None
    assert store.authenticate("测试审核员", new_password)["revision"] != before
    for intruder in (a, b):
        with pytest.raises(AccessDenied):
            store.reset_reviewer_password(intruder, uid, PASSWORD)
        with pytest.raises(AccessDenied):
            store.unlock_reviewer(intruder, uid)
    store.set_active(admin, uid, False)
    store.reset_reviewer_password(admin, uid, PASSWORD)
    store.unlock_reviewer(admin, uid)
    assert store.authenticate("测试审核员", PASSWORD) is None
    with pytest.raises(ValueError):
        store.reset_reviewer_password(admin, admin, PASSWORD)
    assert store.authenticate("admin", PASSWORD)


def diagnostic_run():
    run = ReviewRun(load_case(DATA / "template_validation/regression/03-unfamiliar-invoice-columns"), "zh", "rules")
    run_rules(run)
    return run


def test_normal_route_no_model_and_mapping_requires_explicit_application():
    normal = run_case("A")
    assert not preflight(normal)["needs_help"]
    assert diagnose(normal, "", "unused", "deepseek")["log"] == []
    run = diagnostic_run()
    before = [d.model_dump_json() for d in run.documents]
    context = Context(run)
    context.call(inspect_candidate_tables)
    output = DiagnosticOutput(summary="candidate", mappings=[MappingSuggestion(table="CI:table:0",
        columns=ProposalList(proposals=[Proposal(column=4, field="sku"), Proposal(column=6, field="quantity")]))], fields=[], links=[])
    proposals = validated(context, output)
    assert len(proposals) == 1
    assert [d.model_dump_json() for d in run.documents] == before
    diagnosis = {"revision": revision_key(run), "proposals": proposals}
    revised = apply_proposal(run, diagnosis, 0)
    assert revised.result.counts["difference"] == 2
    assert revised.files == run.files
    with pytest.raises(ValueError, match="数据已经变化"):
        apply_proposal(revised, diagnosis, 0)


def test_field_suggestions_only_from_inspected_exact_source():
    run = run_case()
    invoice = next(d for d in run.documents if d.kind == "CI")
    invoice.header.pop("order_id")
    run.files = [InputFile(f.kind, "CI.csv", b"Order reference,PO-2026\n") if f.kind == "CI" else f for f in run.files]
    context = Context(run)
    response = context.call(inspect_missing_fields)
    assert json.loads(response)["sources"]
    ref = next(k for k, v in context.snippets.items() if v[0] == "CI" and "PO-2026" in v[2])
    quote = context.snippets[ref][2]
    import re
    value = next(v for v in re.findall(r"[A-Za-z0-9][A-Za-z0-9_./-]*", quote) if any(c.isdigit() for c in v))
    output = DiagnosticOutput(summary="candidate", mappings=[], links=[], fields=[
        FieldSuggestion(source=ref, kind="CI", field="order_id", value=value),
        FieldSuggestion(source=ref, kind="CI", field="invoice_id", value="INVENTED-999"),
        FieldSuggestion(source="uninspected", kind="PO", field="order_id", value=value)])
    accepted = validated(context, output)
    assert len(accepted) == 1 and accepted[0]["quote"] == quote
    assert "order_id" not in invoice.header
    revised = apply_proposal(run, {"revision": revision_key(run), "proposals": accepted}, 0)
    assert next(d for d in revised.documents if d.kind == "CI").header["order_id"].edited


def test_sku_links_are_candidates_and_ambiguous_description_is_rejected():
    run = run_case()
    invoice = next(d for d in run.documents if d.kind == "CI")
    invoice.items[0].fields["sku"].raw = "ALT-999"
    context = Context(run)
    response = json.loads(context.call(inspect_item_links))
    assert response["candidates"]
    pair = response["candidates"][0]
    output = DiagnosticOutput(summary="candidate", mappings=[], fields=[], links=[LinkSuggestion(left=pair["left"], right=pair["right"])])
    accepted = validated(context, output)
    assert len(accepted) == 1 and invoice.items[0].fields["sku"].raw == "ALT-999"
    revised = apply_proposal(run, {"revision": revision_key(run), "proposals": accepted}, 0)
    assert revised.files == run.files
    duplicate = invoice.items[0].model_copy(deep=True)
    duplicate.fields["sku"].raw = "OTHER-888"
    invoice.items.append(duplicate)
    assert not json.loads(Context(run).call(inspect_item_links))["candidates"]


@pytest.mark.parametrize("names", [("inspect_candidate_tables", "request_human_review"),
                                  ("inspect_missing_fields", "inspect_item_links", "request_human_review")])
def test_sdk_logs_only_tools_actually_selected_by_model(names):
    from agents import Model, Runner, RunConfig
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    class SelectedTools(Model):
        turn = 0

        async def get_response(self, *args, **kwargs):
            self.turn += 1
            if self.turn <= len(names):
                item = ResponseFunctionToolCall(name=names[self.turn-1], arguments="{}", call_id=f"c{self.turn}", type="function_call")
            else:
                item = ResponseOutputMessage(id="msg", status="completed", role="assistant", type="message",
                    content=[ResponseOutputText(type="output_text", annotations=[], text=DiagnosticOutput(
                        summary="test fixture, not a live model", mappings=[], fields=[], links=[]).model_dump_json())])
            return ModelResponse(output=[item], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise AssertionError("non-streaming")

    context = Context(diagnostic_run())
    result = asyncio.run(Runner.run(make_diagnostic_agent(SelectedTools()), "Inspect this case", context=context,
                                   run_config=RunConfig(tracing_disabled=True)))
    assert [e["tool"] for e in context.log] == list(names)
    assert all(e["status"] == "ok" and e["ms"] >= 0 for e in context.log)
    assert result.final_output.summary.startswith("test fixture")


def test_postgres_storage_when_service_available():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Requires dedicated PostgreSQL CI service; SQLite tests do not count as PostgreSQL validation")
    store = Store(dsn, allow_local=True)
    store.initialize(make_config("admin", PASSWORD))
    test_history_survives_reconnect_and_versions_are_immutable(store)
    a = store.authenticate("reviewer-a", PASSWORD)["id"]
    b = store.authenticate("reviewer-b", PASSWORD)["id"]
    task = store.list_tasks(a)[0]["id"]
    assert store.list_tasks(b) == []
    with pytest.raises(AccessDenied):
        store.load(b, task, 1)
    admin = store.authenticate("admin", PASSWORD)["id"]
    before = store.user(b)["revision"]
    store.reset_reviewer_password(admin, b, "new-postgres-test-password")
    assert store.authenticate("reviewer-b", "new-postgres-test-password")["revision"] != before
    assert store.authenticate("reviewer-b", PASSWORD) is None
    store.unlock_reviewer(admin, b)
    with pytest.raises(AccessDenied):
        store.reset_reviewer_password(a, b, PASSWORD)


def test_ui_saved_task_restores_after_new_login_and_other_account_cannot_see_it(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    config = make_config("admin", PASSWORD)
    for name, value in {"LOGIN_USERNAME": config.username, "LOGIN_SALT": config.salt.hex(),
                        "LOGIN_PASSWORD_HASH": config.digest.hex(), "DATABASE_URL": "",
                        "TRADECHECK_LOCAL_DB": str(tmp_path / "ui.sqlite3")}.items():
        monkeypatch.setenv(name, value)
    path = str(Path(__file__).resolve().parents[1] / "app.py")

    def button(app, label):
        return next(b for b in app.button if b.label == label)

    def login(username, password=PASSWORD):
        app = AppTest.from_file(path).run(timeout=30)
        app.text_input[0].set_value(username)
        app.text_input[1].set_value(password)
        button(app, "登录").click().run(timeout=30)
        assert not app.exception
        assert "_db_login" in app.session_state
        assert app.session_state["_db_login"]["username"] == username
        assert any(r.label == "执行模式" for r in app.radio)
        return app

    app = login("admin")
    next(t for t in app.text_input if t.label == "新账号").set_value("reviewer-ui")
    next(t for t in app.text_input if t.label == "初始密码（至少12位）").set_value(PASSWORD)
    next(t for t in app.text_input if t.label == "确认初始密码").set_value(PASSWORD)
    button(app, "创建审核员账号").click().run(timeout=30)
    assert not app.exception
    new_password = "changed-password-ui-only"
    next(t for t in app.text_input if t.label == "新密码（至少12位）").set_value(new_password)
    next(t for t in app.text_input if t.label == "再次输入新密码").set_value(new_password)
    button(app, "确认重置所选审核员密码").click().run(timeout=30)
    assert not app.exception
    assert any("密码已重置" in m.value for m in app.success)
    next(r for r in app.radio if r.label == "执行模式").set_value("rules").run()
    button(app, "一键加载演示案例").click().run(timeout=30)
    next(t for t in app.text_input if t.label == "任务名称").set_value("持久化验收任务")
    button(app, "保存当前版本").click().run(timeout=30)
    assert not app.exception
    task = app.session_state["task_id"]
    assert app.session_state["task_version"] == 1
    button(app, "保存当前版本").click().run(timeout=30)
    assert app.session_state["task_version"] == 2
    button(app, "退出登录").click().run()
    assert "record" not in app.session_state
    app = login("admin")
    button(app, "打开所选版本").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["task_id"] == task
    assert app.session_state["record"][0].result.counts == {"difference": 3, "contradiction": 1, "review": 0}
    button(app, "退出登录").click().run()
    isolated = login("reviewer-ui", new_password)
    assert not any(s.label == "历史任务" for s in isolated.selectbox)
    assert not any(t.label == "新账号" for t in isolated.text_input)


def test_database_failure_does_not_fall_back_to_legacy_login(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("DATABASE_URL", "invalid://bad")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not app.exception
    assert any("账号服务暂时不可用" in e.value for e in app.error)
    assert not app.button


def test_failed_model_keeps_actual_partial_log_and_never_proposes_changes(monkeypatch):
    import agent.diagnostic_agent as module

    async def failed(context, *args):
        context.call(inspect_candidate_tables)
        raise RuntimeError("private-api-response-not-for-display")

    monkeypatch.setattr(module, "_diagnose", failed)
    run = diagnostic_run()
    original = [d.model_dump_json() for d in run.documents]
    result = diagnose(run, "test-key-not-real", "test-model", "deepseek")
    assert result["mode"] == "failed" and result["proposals"] == []
    assert [e["tool"] for e in result["log"]] == ["inspect_candidate_tables"]
    assert "private-api-response" not in result["error"]
    assert original == [d.model_dump_json() for d in run.documents]


def test_diagnostic_ui_requires_human_checkbox_before_apply(monkeypatch):
    import diagnostic_ui
    from streamlit.testing.v1 import AppTest
    run = diagnostic_run()
    context = Context(run)
    context.call(inspect_candidate_tables)
    proposals = validated(context, DiagnosticOutput(summary="test suggestion", fields=[], links=[], mappings=[
        MappingSuggestion(table="CI:table:0", columns=ProposalList(proposals=[
            Proposal(column=4, field="sku"), Proposal(column=6, field="quantity")]))]))
    result = {"revision": revision_key(run), "summary": "test fixture", "log": context.log, "proposals": proposals}
    monkeypatch.setattr(diagnostic_ui, "diagnose", lambda *args: result)
    script = '''
import streamlit as st
from diagnostic_ui import diagnostic_panel
from agent.tools import ReviewRun, run_rules
from core.demo import DATA, load_case
if "fixture_run" not in st.session_state:
    run = ReviewRun(load_case(DATA / "template_validation/regression/03-unfamiliar-invoice-columns"), "zh", "rules")
    run_rules(run)
    st.session_state["fixture_run"] = run
revised = diagnostic_panel(st.session_state["fixture_run"], "not-a-real-key", "test", "deepseek")
if revised:
    st.session_state["applied"] = revised
'''
    app = AppTest.from_string(script).run(timeout=30)
    button = next(b for b in app.button if b.label == "让 Agent 检查疑难项")
    assert button.disabled
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label == "让 Agent 检查疑难项").click().run(timeout=30)
    assert not app.exception
    assert next(b for b in app.button if b.label == "应用此建议并复审").disabled
    assert "applied" not in app.session_state
    next(c for c in app.checkbox if c.label == "我已对照原件确认这条建议").check().run()
    next(b for b in app.button if b.label == "应用此建议并复审").click().run(timeout=30)
    assert not app.exception
    revised = app.session_state["applied"]
    assert revised.result.counts["difference"] == 2
    assert revised.log[0]["phase"] == "model_diagnosis"
