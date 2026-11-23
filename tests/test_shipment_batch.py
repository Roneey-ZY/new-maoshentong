import csv
import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.shipment_batch import FIELDS, audit_batch, read_batch_csv, make_batch_report
from scripts.generate_batch_validation import generate


def records(folder):
    return [row for kind in FIELDS for row in read_batch_csv(kind, f"{kind}.csv", (folder / f"{kind}.csv").read_bytes())]


@pytest.mark.parametrize("case", ["normal", "partial", "over", "ambiguous"])
def test_partial_business_cases_use_declared_answers(tmp_path, case):
    generate(tmp_path / "batch")
    folder = tmp_path / "batch" / case
    data = records(folder)
    result = audit_batch(data)
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))["expected_nonconsistent_counts"]
    assert {k: result["counts"][k] for k in expected} == expected
    assert all(check["evidence"] for check in result["checks"])
    assert all(e["file"] and e["row"] > 1 for c in result["checks"] for e in c["evidence"])


def csv_bytes(kind, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, FIELDS[kind])
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def test_unknown_units_missing_records_and_currency_never_become_zero(tmp_path):
    generate(tmp_path / "batch")
    rows = records(tmp_path / "batch/normal")
    po = next(r for r in rows if r["kind"] == "PO")
    only_order = audit_batch([po])
    assert only_order["facts"][0]["累计开票数量"] is None and only_order["counts"]["review"] == 1
    ci = next(r for r in rows if r["kind"] == "CI")
    ci["values"].update(unit="UNKNOWN", currency="EUR")
    result = audit_batch(rows)
    assert result["facts"][0]["累计开票数量"] is None
    assert any(c["rule"] == "CURRENCY" and c["status"] == "difference" for c in result["checks"])
    assert any(c["rule"] == "BATCH_UNIT_PRICE" and c["status"] == "review" for c in result["checks"])


def test_same_sku_two_order_lines_are_not_merged_and_ids_are_normalized():
    base = {"order_id": " po-1 ", "order_line_id": "1", "sku": " a-100 ", "quantity": "1 000,00", "unit": "pieces", "currency": "USD", "unit_price": "2.00"}
    orders = [base, {**base, "order_line_id": "2", "quantity": "10"}]
    ci = {**base, "order_id": "PO-1", "invoice_id": "i-1", "invoice_line_id": "1", "quantity": "1000.00", "line_amount": "2000.00"}
    rows = read_batch_csv("PO", "orders.csv", csv_bytes("PO", orders)) + read_batch_csv("CI", "invoices.csv", csv_bytes("CI", [ci]))
    result = audit_batch(rows)
    assert len(result["facts"]) == 2 and result["facts"][0]["累计开票数量"] == "1000.00"
    assert result["facts"][1]["累计开票数量"] is None
    assert not any(c["rule"] == "BATCH_INVOICE_ORDER_QTY" and c["status"] == "difference" for c in result["checks"])


def test_row_order_cannot_change_links_and_excel_keeps_evidence_as_text(tmp_path):
    generate(tmp_path / "batch")
    rows = records(tmp_path / "batch/normal")
    result = audit_batch(list(reversed(rows)))
    assert result["counts"]["review"] == result["counts"]["difference"] == 0
    rows[0]["evidence"]["sku"]["raw"] = '=HYPERLINK("https://example.invalid")'
    workbook = load_workbook(io.BytesIO(make_batch_report(rows, result)))
    assert "字段来源" in workbook.sheetnames
    cells = [cell for row in workbook["字段来源"] for cell in row if str(cell.value).startswith("=HYPERLINK")]
    assert cells and all(c.data_type == "s" for c in cells)


def test_batch_parser_rejects_duplicate_columns_unknown_fields_and_ambiguous_numbers():
    with pytest.raises(ValueError, match="不重复"):
        read_batch_csv("PO", "PO.csv", b"sku,sku\na,b\n")
    with pytest.raises(ValueError, match="模板"):
        read_batch_csv("PL", "GR.csv", b"receipt_id,quantity\na,5\n")
    row = read_batch_csv("PO", "PO.csv", b'order_id,order_line_id,quantity\np,1,"1,000"\n')[0]
    assert row["values"]["quantity"] is None
    with pytest.raises(ValueError, match="引号"):
        read_batch_csv("PO", "PO.csv", b'sku,quantity\n"unterminated,5')


def test_batch_page_can_run_examples_and_download_reports():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string("from batch_ui import batch_panel\nbatch_panel()").run(timeout=30)
    assert not app.exception
    next(b for b in app.button if b.label == "核对分批交易").click().run(timeout=30)
    assert not app.exception and len(app.get("download_button")) == 3
    assert app.session_state["batch_record"]["result"]["counts"]["difference"] == 0
