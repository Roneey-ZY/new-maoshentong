import io
from pathlib import Path

import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from agent.tools import ReviewRun, InputFile, run_rules, revise_preview
from core.demo import DATA, load_case
from core.evidence import original_evidence, pdf_page_image
from core.reporter import make_report
from core.review import ReviewBook, finding_key
from core.authentication import make_config


def case_b():
    run = ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
    run_rules(run)
    return run


def test_original_sources_and_pdf_render():
    run = case_b()
    seen = set()
    for finding in run.result.findings:
        for evidence in finding.evidence:
            file = next(f for f in run.files if f.kind == evidence.kind)
            original = original_evidence(file, evidence.source)
            seen.add(Path(file.name).suffix)
            if file.name.endswith(".pdf"):
                assert original["text"]
                assert pdf_page_image(file, evidence.source.page).startswith(b"\x89PNG")
            else:
                assert any(row["target"] for row in original["rows"])
    # Case B contains spreadsheet formats; case A supplies the PDF fixture.
    from core.parser import parse_document
    file = next(f for f in load_case(DATA / "demo/A") if f.name.endswith(".pdf"))
    doc = parse_document(file.kind, file.name, file.data)
    source = next(v.source for item in doc.items for v in item.fields.values() if v.source.page)
    assert original_evidence(file, source)["text"]
    assert pdf_page_image(file, source.page).startswith(b"\x89PNG")
    seen.add(".pdf")
    assert seen == {".csv", ".xlsx", ".pdf"}


def test_preview_never_changes_original_evidence():
    run = case_b()
    weight = next(f for f in run.result.findings if f.rule == "WEIGHT")
    evidence = next(e for e in weight.evidence if e.field == "gross_kg")
    file = next(f for f in run.files if f.kind == "PL")
    before = original_evidence(file, evidence.source)
    revised = revise_preview(run, "PL", 0, {"gross_kg": "130"})
    assert revised.result.counts["contradiction"] == 0
    assert original_evidence(file, evidence.source) == before
    assert revised.files == run.files


def test_reason_is_annotation_not_rule_override():
    run = case_b()
    book = ReviewBook()
    difference = next(f for f in run.result.findings if f.category == "difference")
    book.set_shipment("partial", "judge")
    book.record(difference, run.files, "explained", "partial_shipment", "对照客户邮件：本批先发九十件。", "judge")
    assert book.current(difference, run.files).status == "explained"
    assert run.result.counts == {"difference": 3, "contradiction": 1, "review": 0}
    contradiction = next(f for f in run.result.findings if f.category == "contradiction")
    with pytest.raises(ValueError, match="不能直接"):
        book.record(contradiction, run.files, "explained", "other", "称重记录需要检查", "judge")
    with pytest.raises(ValueError, match="至少"):
        book.record(difference, run.files, "explained", "other", "", "judge")


def test_disposition_bound_to_evidence_not_display_id():
    run = case_b()
    finding = next(f for f in run.result.findings if f.rule == "PO_CI_QTY")
    book = ReviewBook()
    book.record(finding, run.files, "explained", "partial_shipment", "客户确认本批部分交付", "judge")
    renumbered = finding.model_copy(update={"id": "F999"})
    assert book.current(renumbered, run.files)
    changed = finding.model_copy(deep=True)
    changed.evidence[0].value = "999"
    assert book.current(changed, run.files) is None
    replaced = [InputFile(f.kind, f.name, f.data + b"\n") for f in run.files]
    assert book.current(finding, replaced) is None


@pytest.mark.parametrize("lang,sheet", [("zh", "业务复核"), ("en", "Human review")])
def test_report_keeps_all_findings_and_review_history(lang, sheet):
    run = case_b()
    book = ReviewBook()
    first = run.result.findings[0]
    note = '=HYPERLINK("https://example.invalid", "text")'
    book.record(first, run.files, "explained", "other", note, "judge")
    report = make_report(run.documents, run.result, lang, "rules", book, run.files)
    wb = load_workbook(io.BytesIO(report))
    assert wb["Findings" if lang == "en" else "异常与复核"].max_row == 5
    cells = [cell for row in wb[sheet] for cell in row if cell.value == note]
    assert len(cells) == 1 and cells[0].data_type == "s"
    assert wb["Review history" if lang == "en" else "复核历史"].max_row == 2


def test_reaudit_does_not_silently_reuse_changed_decision():
    run = case_b()
    finding = next(f for f in run.result.findings if f.rule == "PO_CI_QTY")
    book = ReviewBook()
    book.record(finding, run.files, "explained", "partial_shipment", "客户确认本批部分交付", "judge")
    revision = revise_preview(run, "CI", 0, {"quantity": "80"})
    changed = next(f for f in revision.result.findings if f.rule == "PO_CI_QTY")
    assert book.current(changed, revision.files) is None
    report = make_report(revision.documents, revision.result, "zh", "revision", book, revision.files)
    wb = load_workbook(io.BytesIO(report))
    assert any(row[0] == "不再对应当前发现" for row in wb["复核历史"].values)


def test_ui_evidence_and_business_review(monkeypatch):
    config = make_config("judge", "test-password-phase-A")
    for key, value in {"LOGIN_USERNAME": config.username, "LOGIN_SALT": config.salt.hex(),
                       "LOGIN_PASSWORD_HASH": config.digest.hex()}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    app.text_input[0].set_value("judge")
    app.text_input[1].set_value("test-password-phase-A")
    app.button[0].click().run(timeout=30)
    next(r for r in app.radio if r.label == "执行模式").set_value("rules").run()
    next(b for b in app.button if b.label == "一键加载演示案例").click().run(timeout=30)
    assert not app.exception
    next(b for b in app.button if b.label == "读取原始证据").click().run(timeout=30)
    assert not app.exception
    next(s for s in app.selectbox if s.label == "复核状态").set_value("explained")
    next(t for t in app.text_area if t.label == "复核说明及依据").set_value("客户邮件确认本批先发九十件。")
    next(b for b in app.button if b.label == "保存复核记录").click().run(timeout=30)
    assert not app.exception
    assert len(app.session_state["review_book"].decisions) == 1
    assert len(app.session_state["record"][0].result.findings) == 4
    next(b for b in app.button if b.label == "退出登录").click().run()
    assert "review_book" not in app.session_state
