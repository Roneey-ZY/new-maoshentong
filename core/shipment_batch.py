"""Explicit line-key matching for partial cross-border shipments, separate from GR data.

All totals refer only to supplied records, never to invoice-date delivery or real
completion. Ambiguous/invalid members block the corresponding cumulative check.
"""
import csv
import io
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

from core.normalizer import normalize_value, CURRENCY_SCALE
from core.schemas import Source, FieldValue

POLICY = "shipment-batch-v1"
FIELDS = {
    "PO": ("order_id", "order_line_id", "sku", "quantity", "unit", "currency", "unit_price"),
    "CI": ("invoice_id", "invoice_line_id", "order_id", "order_line_id", "sku", "quantity", "unit", "currency", "unit_price", "line_amount"),
    "PL": ("packing_id", "packing_line_id", "invoice_id", "invoice_line_id", "order_id", "order_line_id", "sku", "quantity", "unit", "net_kg", "gross_kg"),
}
IDS = {"PO": ("order_id", "order_line_id"), "CI": ("invoice_id", "invoice_line_id"), "PL": ("packing_id", "packing_line_id")}
SCOPE = "仅比较本次提供记录的累计数量；少于订单或发票可为分批，未提供记录不能视为零。收货记录不能替代装箱单。"


def read_batch_csv(kind, filename, data):
    if kind not in FIELDS or not filename.lower().endswith(".csv") or len(data) > 8 * 1024 * 1024:
        raise ValueError("批次入口只接收每份不超过8 MiB的CSV模板。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("请将CSV保存为UTF-8编码。") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV须包含不重复的模板列名。")
    if set(reader.fieldnames) - set(FIELDS[kind]):
        raise ValueError("请使用下载的模板列名；不支持的列请先人工对应。")
    result = []
    previous = reader.line_num
    for raw in reader:
        if len(result) >= 2000 or None in raw or any(v is None for v in raw.values()):
            raise ValueError("CSV行数超过2000或列数不一致。")
        values, evidence = {}, {}
        for key in FIELDS[kind]:
            value = raw.get(key, "")
            if len(value) > 1000:
                raise ValueError("单个字段超过1000字符。")
            source = Source(file=filename, row=previous + 1, column=reader.fieldnames.index(key) + 1 if key in reader.fieldnames else None)
            field = key if key in {"sku", "quantity", "unit", "currency", "unit_price", "line_amount", "net_kg", "gross_kg"} else "order_id"
            values[key] = normalize_value(field, FieldValue(raw=value, source=source))
            evidence[key] = {**source.model_dump(), "field": key, "raw": value,
                             "column_name": key, "column_present": key in reader.fieldnames}
        result.append({"kind": kind, "values": values, "evidence": evidence})
        previous = reader.line_num
    return result


def audit_batch(records):
    checks, facts = [], []
    def add(rule, status, detail, rows, fields):
        checks.append({"rule": rule, "status": status, "detail": detail,
            "evidence": [row["evidence"][f] for row in rows for f in fields if f in row["evidence"]]})
    def key(row, fields):
        return tuple(row["values"].get(f) for f in fields)
    def numeric(row, field):
        value = row["values"].get(field)
        return value if isinstance(value, Decimal) and value >= 0 else None
    def unit(row):
        value = row["values"].get("unit")
        return value if value in {"PCS", "KG", "SET", "BOX"} else None
    def valid_quantity(row):
        return numeric(row, "quantity") is not None and unit(row) is not None
    grouped = {kind: [r for r in records if r["kind"] == kind] for kind in FIELDS}
    blocked = set()
    for kind, rows in grouped.items():
        counts = Counter(key(r, IDS[kind]) for r in rows)
        for row in rows:
            identity = key(row, IDS[kind])
            if not all(identity) or counts[identity] != 1:
                blocked.add(id(row))
                add("RECORD_ID", "review", "记录标识缺失或重复；保留原行，不静默去重或累计。", [row], IDS[kind])
    po_index = defaultdict(list)
    for row in grouped["PO"]:
        po_index[key(row, IDS["PO"])].append(row)
    invoices = defaultdict(list)
    invalid_orders = set()
    usable_ci = {}
    for row in grouped["CI"]:
        order_key = key(row, IDS["PO"])
        linked = po_index.get(order_key, [])
        if id(row) in blocked or not all(order_key) or len(linked) != 1 or id(linked[0]) in blocked:
            invalid_orders.add(order_key)
            add("ORDER_LINE_LINK", "review", "发票不能唯一关联采购行，请核对订单号与采购行编号。", [row, *linked], (*IDS["PO"], "sku"))
        else:
            po = linked[0]
            invoices[order_key].append(row)
            usable_ci[key(row, IDS["CI"])] = row
            same_sku = bool(key(row, ("sku",))[0]) and key(row, ("sku",)) == key(po, ("sku",))
            if not same_sku:
                invalid_orders.add(order_key)
                add("SKU_LINK", "review", "行标识已关联，但商品编码缺失或不同，数量与单价不可直接比较。", [po, row], ("sku",))
            price, ordered_price = numeric(row, "unit_price"), numeric(po, "unit_price")
            currency, po_currency = row["values"].get("currency"), po["values"].get("currency")
            if currency is None or po_currency is None:
                add("CURRENCY", "review", "币种缺失，无法核验币种或单价。", [po, row], ("currency",))
            elif currency != po_currency:
                add("CURRENCY", "difference", "采购与发票币种不同，不作汇率换算或跨币种单价比较。", [po, row], ("currency",))
            if same_sku and currency == po_currency and currency in CURRENCY_SCALE and price is not None and ordered_price is not None and unit(row) and unit(row) == unit(po):
                add("BATCH_UNIT_PRICE", "consistent" if price == ordered_price else "difference", "比较本张发票与采购行的同币种、同单位单价。", [po, row], ("unit_price", "currency", "unit"))
            else:
                add("BATCH_UNIT_PRICE", "review", "缺少可比较的商品、币种、单位或有效单价，无法核验单价。", [po, row], ("sku", "unit_price", "currency", "unit"))
        qty, price, amount = (numeric(row, f) for f in ("quantity", "unit_price", "line_amount"))
        currency = row["values"].get("currency")
        if any(v is None for v in (qty, price, amount)) or currency not in CURRENCY_SCALE:
            add("BATCH_LINE_AMOUNT", "review", "缺少有效数量、单价、行金额或已知币种精度，无法核验乘积。", [row], ("quantity", "unit_price", "line_amount", "currency"))
        else:
            expected = (qty * price).quantize(Decimal(1).scaleb(-CURRENCY_SCALE[currency]), rounding=ROUND_HALF_UP)
            add("BATCH_LINE_AMOUNT", "consistent" if expected == amount else "difference", f"数量乘单价按{currency}最小货币单位四舍五入为{expected}；仅核对行金额，不推断税费折扣。", [row], ("quantity", "unit_price", "line_amount", "currency"))
    for po in grouped["PO"]:
        order_key = key(po, IDS["PO"])
        members = invoices.get(order_key, [])
        good = (id(po) not in blocked and order_key not in invalid_orders and bool(members)
                and valid_quantity(po) and all(valid_quantity(r) and unit(r) == unit(po) for r in members))
        total = sum((numeric(r, "quantity") for r in members), Decimal(0)) if good else None
        ordered = numeric(po, "quantity")
        facts.append({"订单": order_key[0], "采购行": order_key[1], "SKU": po["values"].get("sku"),
                      "采购数量": str(ordered) if ordered is not None else None,
                      "累计开票数量": str(total) if total is not None else None, "开票行数": len(members)})
        if total is None:
            status, detail = "review", "没有发票，或关联/数字/单位不完整，无法核验累计数量；缺失不能当作零。"
        else:
            status = "difference" if total > ordered else "info" if total < ordered else "consistent"
            detail = f"采购{ordered}，本次记录累计开票{total}。" + ("少于采购，可为分批开票。" if total < ordered else "超过采购，需核对补充订单或录入。" if total > ordered else "数量相符。")
        add("BATCH_INVOICE_ORDER_QTY", status, detail, [po, *members], (*IDS["PO"], "sku", "quantity", "unit"))
    packing = defaultdict(list)
    invalid_invoices = set()
    for row in grouped["PL"]:
        identity = key(row, IDS["CI"])
        target = usable_ci.get(identity)
        if id(row) in blocked or target is None or key(row, (*IDS["PO"], "sku")) != key(target, (*IDS["PO"], "sku")):
            invalid_invoices.add(identity)
            add("PACKING_INVOICE_LINK", "review", "装箱行不能唯一关联发票行，或订单/商品不一致；请补齐正确行标识。", [row], (*IDS["CI"], *IDS["PO"], "sku"))
        else:
            packing[identity].append(row)
        net, gross = numeric(row, "net_kg"), numeric(row, "gross_kg")
        status = "review" if net is None or gross is None else "contradiction" if gross < net else "consistent"
        add("BATCH_WEIGHT", status, "仅核对每条装箱记录的非负净重和毛重；毛重不得小于净重，缺值或无效数字需复核。", [row], ("net_kg", "gross_kg"))
    for identity, invoice in usable_ci.items():
        members = packing.get(identity, [])
        good = bool(members) and identity not in invalid_invoices and valid_quantity(invoice) and all(
            valid_quantity(r) and unit(r) == unit(invoice) for r in members)
        total = sum((numeric(r, "quantity") for r in members), Decimal(0)) if good else None
        qty = numeric(invoice, "quantity")
        if total is None:
            status, detail = "review", "没有可完整关联且单位一致的装箱记录，无法核验；不把收货记录代作装箱单。"
        else:
            status = "difference" if total > qty else "info" if total < qty else "consistent"
            detail = f"发票行数量{qty}，本次记录累计装箱{total}。" + ("少于发票，可为分批装箱，需确认是否还有未提供记录。" if total < qty else "超过发票数量，需核对关联或补充单证。" if total > qty else "数量相符。")
        add("BATCH_PACKING_INVOICE_QTY", status, detail, [invoice, *members], (*IDS["CI"], "quantity", "unit"))
    if not grouped["PO"]:
        add("BATCH_INPUT", "review", "未提供采购行，无法执行累计采购核验。", [], ())
    return {"policy": POLICY, "scope": SCOPE, "facts": facts, "checks": checks,
        "counts": {status: sum(c["status"] == status for c in checks) for status in ("consistent", "difference", "contradiction", "review", "info")},
        "model_calls": 0}


def make_batch_report(records, result):
    import json
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = "范围"
    sheet.append(["政策版本", result["policy"]])
    sheet.append(["范围", result["scope"]])
    sheet.append(["执行方式", "确定性Python规则，无模型调用；不提供法律合规保证。"])
    sheet = book.create_sheet("核验明细")
    sheet.append(["规则", "状态", "说明", "证据"])
    for check in result["checks"]:
        sheet.append([check["rule"], check["status"], check["detail"], json.dumps(check["evidence"], ensure_ascii=False)])
    sheet = book.create_sheet("字段来源")
    sheet.append(["单证", "文件", "行", "列", "字段", "原值"])
    for record in records:
        for name, source in record["evidence"].items():
            sheet.append([record["kind"], source["file"], source["row"], source["column"], name, source["raw"]])
    for sheet in book:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # Imported user text must not become Excel formulae.
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="132B49")
            cell.font = Font(color="FFFFFF", bold=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 28
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
