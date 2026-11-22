import copy
import io
import json
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from core.procurement import DATASET, read_tables, convert_order, audit_order, number, validate_links
from core.procurement_report import make_procurement_report
from scripts.evaluate_procurement import run_orders, pilot, freeze, evaluate


def base_line():
    tables, _ = read_tables()
    order = convert_order(tables, "1")
    # Keep real source locators, replace only quantities in this labelled unit fixture.
    line = copy.deepcopy(order["lines"][0])
    line["ordered_quantity"] = "100"
    line["po_unit_price"] = "1.25"
    ref = line["origins"]["line_id"]
    def receipt(identity, qty):
        return {"receipt_id": identity, "received_quantity": qty, "condition": "good",
                "origins": {"received_quantity": ref, "condition": ref}}
    def invoice(identity, qty):
        return {"invoice_id": identity, "invoice_number": "INV-" + identity, "invoiced_quantity": qty, "unit_price": "1.25",
                "origins": {"invoiced_quantity": ref, "unit_price": ref, "invoice_number": ref}}
    line["receipts"] = [receipt("r1", "40"), receipt("r2", "60")]
    line["invoices"] = [invoice("i1", "40"), invoice("i2", "60")]
    order["lines"] = [line]
    return order


def test_public_source_integrity_and_five_order_pilot(tmp_path):
    tables, source = read_tables()
    assert sum(len(rows) for rows in tables.values()) == 492
    assert len(tables["purchase_orders"]) == 36 and source["license"] == "CC0-1.0"
    measured = pilot(output=tmp_path / "pilot")
    assert measured["summary"]["orders"] == measured["summary"]["reference_agreement_orders"] == 5
    assert measured["summary"]["origin_errors"] == 0
    assert measured["summary"]["model_calls"] == 0


def test_partial_receipts_and_multiple_invoices_are_not_automatically_errors():
    order = base_line()
    result = audit_order(order)
    assert result["counts"]["difference"] == 0
    assert any(row["rule"] == "MULTIPLE_INVOICES" and row["status"] == "info" for row in result["checks"])
    assert result["facts"][0]["received_quantity"] == "100"
    order["lines"][0]["receipts"] = order["lines"][0]["receipts"][:1]
    order["lines"][0]["invoices"] = order["lines"][0]["invoices"][:1]
    result = audit_order(order)
    assert result["counts"]["difference"] == 0
    assert any(row["rule"] == "RECEIPT_ORDER_QTY" and row["status"] == "info" for row in result["checks"])


def test_missing_is_not_zero_and_overbilling_uses_all_records():
    order = base_line()
    order["lines"][0]["receipts"] = []
    result = audit_order(order)
    assert result["facts"][0]["received_quantity"] is None
    assert any(row["rule"] == "INVOICE_RECEIPT_QTY" and row["status"] == "review" for row in result["checks"])
    assert not any(row["rule"] == "INVOICE_RECEIPT_QTY" and row["status"] == "difference" for row in result["checks"])
    order["lines"][0]["invoices"][1]["invoiced_quantity"] = "70"
    result = audit_order(order)
    assert any(row["rule"] == "INVOICE_ORDER_QTY" and row["status"] == "difference" for row in result["checks"])


def test_same_sku_different_order_line_is_not_merged_and_generator_hints_are_excluded():
    order = base_line()
    second = copy.deepcopy(order["lines"][0])
    second["line_id"] = "different-line"
    second["invoices"] = []
    order["lines"].append(second)
    result = audit_order(order)
    assert len(result["facts"]) == 2
    assert result["facts"][0]["invoiced_quantity"] == "100"
    assert result["facts"][1]["invoiced_quantity"] is None
    original = convert_order(read_tables()[0], "1")
    payload = json.dumps(original)
    assert all(hint not in payload for hint in ["qty_ratio", "billing_drift", "received_share", "negotiated_factor"])
    assert all(row["rule"] not in {"WEIGHT", "LINE_AMOUNT", "INVOICE_TOTAL"} for row in audit_order(original)["checks"])


def test_foreign_key_failures_are_not_guessed_and_numeric_values_are_exact():
    tables, _ = read_tables()
    tables = copy.deepcopy(tables)
    tables["goods_receipts"][0]["po_line_id"] = "not-an-order-line"
    with pytest.raises(ValueError, match="外键"):
        validate_links(tables)
    assert number("0.1") + number("0.2") == Decimal("0.3")
    for value in ["NaN", "Infinity", "-1"]:
        with pytest.raises(ValueError):
            number(value)


def test_independent_reference_detects_a_broken_auditor(tmp_path):
    def broken(order):
        result = audit_order(order)
        result["checks"] = []
        return result
    result = run_orders(DATASET, tmp_path / "bad", [("1", "test-fixture")], "deliberately_broken_test", broken)
    assert result["summary"]["reference_agreement_orders"] == 0
    assert result["summary"]["missing_reference_checks"] > 0


def test_excel_report_keeps_missing_values_and_does_not_execute_text():
    tables, source = read_tables()
    order = convert_order(tables, "1")
    result = audit_order(order)
    order["po_number"] = '=HYPERLINK("https://example.invalid", "x")'
    workbook = load_workbook(io.BytesIO(make_procurement_report(order, result, source)))
    assert workbook["范围与来源"]["B2"].data_type == "s"
    assert "规则适用范围" in workbook.sheetnames and "转换溯源" in workbook.sheetnames
    rows = list(workbook["规则适用范围"].values)
    assert any(row[0] == "装箱重量和包装件数" and row[1] == "不适用" for row in rows)


def test_order_level_split_preserves_every_child_and_checksum_failures_stop_import(tmp_path):
    tables, _ = read_tables()
    protocol = json.loads((DATASET / "protocol.json").read_text(encoding="utf-8"))
    development, holdout = set(protocol["development_order_ids"]), set(protocol["holdout_order_ids"])
    assert len(development) == 12 and len(holdout) == 24 and not development & holdout
    assert set(protocol["pilot_order_ids"]) <= development
    converted = [convert_order(tables, row["id"]) for row in tables["purchase_orders"]]
    lines = [line for order in converted for line in order["lines"]]
    assert len(lines) == len({line["line_id"] for line in lines}) == 91
    assert sum(len(line["receipts"]) for line in lines) == 89
    assert sum(len(line["invoices"]) for line in lines) == 87
    broken = tmp_path / "tampered"
    (broken / "raw").mkdir(parents=True)
    (broken / "source.json").write_bytes((DATASET / "source.json").read_bytes())
    (broken / "raw/buyers.csv").write_bytes((DATASET / "raw/buyers.csv").read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="校验失败"):
        read_tables(broken)


def test_frozen_reference_and_browser_execution_with_downloads(tmp_path):
    result = evaluate(output=tmp_path / "repeat")
    assert result["summary"]["orders"] == result["summary"]["reference_agreement_orders"] == 36
    assert result["summary"]["conversion_field_differences"] == 0
    assert result["summary"]["origin_errors"] == 0
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string("from procurement_ui import procurement_panel\nprocurement_panel()").run(timeout=30)
    assert not app.exception
    assert any("不是装箱单" in message.value for message in app.warning)
    assert len(app.get("download_button")) == 2
    next(b for b in app.button if b.label == "执行本订单采购规则核对").click().run(timeout=30)
    assert not app.exception
    state = app.session_state["procurement_result"]
    assert [event["状态"] for event in state["events"]] == ["成功"] * 4
    assert state["result"]["model_calls"] == 0
    assert len(app.get("download_button")) == 4
