"""Separate procurement/receipt snapshot checks, never packing-list impersonation."""
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATASET = Path(__file__).resolve().parents[1] / "data/external/databasesample/procurement-v1"
TABLES = ("buyers", "suppliers", "items", "requisitions", "purchase_orders", "po_lines", "goods_receipts", "supplier_invoices")
POLICY = "procurement-snapshot-v1"
APPLICABILITY = [
    {"rule": "采购与开票单价", "status": "可核验", "basis": "同一采购行；金额单位依来源文档的 USD 说明，不推断逐单币种"},
    {"rule": "累计开票 / 采购数量", "status": "可核验", "basis": "全量记录快照，超过为业务差异，少于可为分批开票"},
    {"rule": "累计收货 / 采购数量", "status": "可核验", "basis": "全量记录快照；无记录不能推断实际收货为零"},
    {"rule": "累计开票 / 累计收货", "status": "条件核验", "basis": "仅两侧有记录时比较；无法判断某张发票对应某次收货或开票当日是否已到货"},
    {"rule": "发票行金额或总金额算术", "status": "不适用", "basis": "来源没有独立记录金额，不能补出乘积再自证正确"},
    {"rule": "装箱重量和包装件数", "status": "不适用", "basis": "收货记录不是装箱单，来源没有净重、毛重或包装件数"},
    {"rule": "逐单币种一致性", "status": "无法核验", "basis": "来源说明价格为美元，但没有逐单币种字段"},
    {"rule": "验收合格数量及付款条件", "status": "无法核验", "basis": "没有合格验收数量、付款金额或完整结算依据"},
]


def number(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("采购数据包含无法解释的数字") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("采购数据中的数量和单价须为有限非负数")
    return parsed


def read_tables(folder=DATASET):
    source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    tables = {}
    for name in TABLES:
        content = (folder / "raw" / f"{name}.csv").read_bytes()
        metadata = source["tables"][name]
        if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise ValueError(f"原始来源文件校验失败：{name}")
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        _ = reader.fieldnames
        previous = reader.line_num
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV 列数不一致：{name}")
            rows.append({**row, "_source": {"file": f"raw/{name}.csv", "row": previous + 1,
                                           "end_row": reader.line_num, "record_id": row["id"]}})
            previous = reader.line_num
        if len(rows) != metadata["rows"]:
            raise ValueError(f"来源行数不一致：{name}")
        ids = [row["id"] for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError(f"来源主键重复：{name}")
        tables[name] = rows
    validate_links(tables)
    return tables, source


def validate_links(tables):
    indexes = {name: {row["id"] for row in values} for name, values in tables.items()}
    relations = {"requisitions": {"item_id": "items"},
        "purchase_orders": {"supplier_id": "suppliers", "buyer_id": "buyers"},
        "po_lines": {"purchase_order_id": "purchase_orders", "requisition_id": "requisitions", "item_id": "items"},
        "goods_receipts": {"po_line_id": "po_lines"}, "supplier_invoices": {"po_line_id": "po_lines"}}
    for table, keys in relations.items():
        for row in tables[table]:
            for field, target in keys.items():
                if row[field] not in indexes[target]:
                    raise ValueError(f"外键无法关联：{table}.{field}，记录 {row['id']}")


def origin(row, column, method="direct"):
    return {**row["_source"], "column": column, "raw": row[column], "method": method}


def convert_order(tables, order_id):
    indexes = {name: {row["id"]: row for row in rows} for name, rows in tables.items()}
    order = indexes["purchase_orders"][str(order_id)]
    supplier, buyer = indexes["suppliers"][order["supplier_id"]], indexes["buyers"][order["buyer_id"]]
    result = {"order_id": order["id"], "po_number": order["po_number"],
        "source_type": "third_party_public_synthetic_not_real_enterprise", "policy": POLICY,
        "scope": "all_records_snapshot_not_per_shipment_or_invoice_date",
        "supplier": supplier["supplier_name"], "buyer": buyer["first_name"] + " " + buyer["last_name"],
        "origins": {"po_number": origin(order, "po_number"), "supplier": origin(supplier, "supplier_name", "master_lookup"),
                    "buyer_first_name": origin(buyer, "first_name", "master_lookup"),
                    "buyer_last_name": origin(buyer, "last_name", "master_lookup")}, "lines": []}
    for row in tables["po_lines"]:
        if row["purchase_order_id"] != order["id"]:
            continue
        item = indexes["items"][row["item_id"]]
        line = {"line_id": row["id"], "item_id": row["item_id"], "sku": item["sku"],
            "product": item["item_label"], "unit": item["unit"], "ordered_quantity": row["quantity"],
            "po_unit_price": row["unit_price"], "receipts": [], "invoices": [],
            "origins": {"line_id": origin(row, "id"), "purchase_order_id": origin(row, "purchase_order_id"),
                "sku": origin(item, "sku", "master_lookup"), "product": origin(item, "item_label", "master_lookup"),
                "unit": origin(item, "unit", "master_lookup"), "ordered_quantity": origin(row, "quantity"),
                "po_unit_price": origin(row, "unit_price")}}
        for table, key, fields in [
            ("goods_receipts", "receipts", {"receipt_id": "id", "received_quantity": "received_qty", "received_on": "received_on", "condition": "condition"}),
            ("supplier_invoices", "invoices", {"invoice_id": "id", "invoice_number": "invoice_no", "invoiced_quantity": "invoiced_qty", "unit_price": "billed_unit_price", "invoice_date": "invoice_date"})]:
            for record in tables[table]:
                if record["po_line_id"] == row["id"]:
                    line[key].append({**{target: record[source] for target, source in fields.items()},
                        "origins": {**{target: origin(record, source) for target, source in fields.items()},
                                    "po_line_id": origin(record, "po_line_id")}})
        for field in ("ordered_quantity", "po_unit_price"):
            number(line[field])
        for record in line["receipts"]:
            number(record["received_quantity"])
        for record in line["invoices"]:
            number(record["invoiced_quantity"])
            number(record["unit_price"])
        result["lines"].append(line)
    if not result["lines"]:
        raise ValueError("订单没有可核对的采购行")
    return result


def audit_order(order):
    checks, facts = [], []

    def add(line, rule, status, detail, evidence, record_id=""):
        checks.append({"line_id": line["line_id"], "sku": line["sku"], "rule": rule,
            "status": status, "record_id": record_id, "detail": detail, "evidence": evidence})

    for line in order["lines"]:
        ordered = number(line["ordered_quantity"])
        receipts, invoices = line["receipts"], line["invoices"]
        received = sum((number(r["received_quantity"]) for r in receipts), Decimal(0)) if receipts else None
        billed = sum((number(r["invoiced_quantity"]) for r in invoices), Decimal(0)) if invoices else None
        facts.append({"line_id": line["line_id"], "sku": line["sku"], "unit": line["unit"],
            "ordered_quantity": str(ordered), "received_quantity": str(received) if received is not None else None,
            "invoiced_quantity": str(billed) if billed is not None else None,
            "receipt_records": len(receipts), "invoice_records": len(invoices)})
        for value, records, field, rule in [(received, receipts, "received_quantity", "RECEIPT_ORDER_QTY"),
                                            (billed, invoices, "invoiced_quantity", "INVOICE_ORDER_QTY")]:
            evidence = [line["origins"]["ordered_quantity"], *[r["origins"][field] for r in records]]
            if value is None:
                add(line, rule, "review", "缺少一侧记录，无法核验；不能把未提供的记录当作实际数量为零。", evidence)
            else:
                status = "difference" if value > ordered else "info" if value < ordered else "consistent"
                text = f"全量记录累计 {value}，采购 {ordered}。"
                text += " 超过采购量，需核对补充订单或录入。" if value > ordered else " 少于采购量，可为分批，不能直接认定错误。" if value < ordered else " 数量相同。"
                add(line, rule, status, text, evidence)
        evidence = [r["origins"]["received_quantity"] for r in receipts] + [r["origins"]["invoiced_quantity"] for r in invoices]
        if received is None or billed is None:
            add(line, "INVOICE_RECEIPT_QTY", "review", "收货或开票记录缺失，无法比较累计数量。", evidence)
        else:
            status = "difference" if billed > received else "info" if billed < received else "consistent"
            add(line, "INVOICE_RECEIPT_QTY", status,
                f"快照累计开票 {billed}，收货 {received}。未关联具体批次，不能据此判断开票当日是否已到货或认定违规。", evidence)
        for invoice in invoices:
            price = number(invoice["unit_price"])
            add(line, "UNIT_PRICE", "consistent" if price == number(line["po_unit_price"]) else "difference",
                f"采购单价 {line['po_unit_price']}，本张发票单价 {invoice['unit_price']}，按来源文档的 USD 口径比较；不同需核对调价依据。",
                [line["origins"]["po_unit_price"], invoice["origins"]["unit_price"]], invoice["invoice_id"])
        if not invoices:
            add(line, "UNIT_PRICE", "review", "没有发票单价，无法核验。", [line["origins"]["po_unit_price"]])
        if len(invoices) > 1:
            add(line, "MULTIPLE_INVOICES", "info", "同一采购行有多张不同编号发票；保留所有记录，并检查累计数量，不自动认定重复开票。",
                [record["origins"]["invoice_number"] for record in invoices])
        for receipt in receipts:
            add(line, "RECEIPT_CONDITION", "consistent" if receipt["condition"] == "good" else "review",
                f"收货状态：{receipt['condition']}；没有合格验收数量，不能据此计算应付数量。",
                [receipt["origins"]["condition"]], receipt["receipt_id"])
    return {"order_id": order["order_id"], "po_number": order["po_number"], "policy": POLICY,
        "scope": order["scope"], "facts": facts, "checks": checks,
        "counts": {key: sum(row["status"] == key for row in checks) for key in ("consistent", "difference", "review", "info")},
        "applicability": APPLICABILITY, "model_calls": 0,
        "note": "这是第三方公开合成采购数据的确定性规则核对，不是企业实证、装箱单验证或模型准确率。"}
