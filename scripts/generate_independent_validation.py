"""Freeze new synthetic layouts and declared answers without invoking the app parser/rules.

This is a template holdout exercise, not independent human validation or real-company data.
Re-running into an existing directory is deliberately forbidden.
"""
import argparse
import copy
import csv
import hashlib
import json
import random
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from reportlab.lib.pagesizes import landscape, A3
from reportlab.pdfgen.canvas import Canvas

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT = PROJECT / "data/independent_validation/v1"
KINDS = ("PO", "CI", "PL")
KEYS = {
    "PO": ["order_id", "buyer", "seller", "currency", "sku", "product", "quantity", "unit", "unit_price"],
    "CI": ["order_id", "invoice_id", "buyer", "seller", "currency", "sku", "product", "quantity", "unit", "unit_price", "line_amount"],
    "PL": ["order_id", "buyer", "seller", "sku", "product", "quantity", "unit", "packages", "net_kg", "gross_kg"],
}
ENGLISH = {"sku": "Merchandise identity", "quantity": "Count billed this shipment",
           "unit_price": "Price charged per piece", "line_amount": "Extended item value"}
CHINESE = {"sku": "产品目录代号", "quantity": "此次开票件数", "unit_price": "每件结算价格", "line_amount": "本项结算金额"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def values(seed, rng):
    order = f"NX-{7800 + seed}"
    sku = f"XC-{4500 + seed}"
    quantity = rng.randint(17, 73)
    price = Decimal(rng.choice(["3.25", "7.80", "16.45", "24.10"]))
    base = {"order_id": order, "buyer": "Synthetic Harbor Buyer", "seller": "Synthetic Ridge Seller",
            "sku": sku, "product": "Connector set", "quantity": str(quantity), "unit": "PCS"}
    po = {**base, "currency": "USD", "unit_price": str(price)}
    ci = {**po, "invoice_id": f"NXI-{9800 + seed}", "line_amount": str(quantity * price)}
    pl = {**base, "packages": "4", "net_kg": "32.5", "gross_kg": "37.0"}
    return {"PO": [po], "CI": [ci], "PL": [pl]}


def scenario(rows, name):
    """Answers are declared from the constructed scenario, never from audit output."""
    ci, pl = rows["CI"][0], rows["PL"][0]
    sku = ci["sku"]
    if name == "quantity":
        ci["quantity"] = str(int(ci["quantity"]) - 3)
        ci["line_amount"] = str(Decimal(ci["quantity"]) * Decimal(ci["unit_price"]))
        return [["PO_CI_QTY", "difference", sku], ["CI_PL_QTY", "difference", sku]]
    if name == "price":
        ci["unit_price"] = str(Decimal(ci["unit_price"]) + Decimal("0.60"))
        ci["line_amount"] = str(Decimal(ci["quantity"]) * Decimal(ci["unit_price"]))
        return [["UNIT_PRICE", "difference", sku]]
    if name == "amount":
        ci["line_amount"] = str(Decimal(ci["line_amount"]) + Decimal("1.20"))
        return [["LINE_AMOUNT", "contradiction", sku]]
    if name == "weight":
        pl["gross_kg"] = "30.0"
        return [["WEIGHT", "contradiction", sku]]
    if name == "missing_weight":
        pl.pop("gross_kg")
        return [["WEIGHT", "review", sku]]
    if name == "order_conflict":
        pl["order_id"] = "NX-UNLINKED-001"
        return [["ORDER_LINK", "difference", ""]]
    if name == "duplicate":
        rows["CI"].append(copy.deepcopy(ci))
        return [["SKU_DUPLICATE", "review", sku]]
    if name == "unit_unknown":
        ci["unit"] = "unverified-unit"
        return [["PO_CI_QTY", "review", sku], ["UNIT_PRICE", "review", sku], ["CI_PL_QTY", "review", sku]]
    return []


def write_document(folder, kind, rows, layout, confirmations):
    keys = list(KEYS[kind])
    labels = keys.copy()
    prefix = []
    if kind == "CI" and layout in {"unfamiliar-en", "unfamiliar-zh"}:
        aliases = ENGLISH if layout == "unfamiliar-en" else CHINESE
        labels = [aliases.get(key, key) for key in keys]
        confirmations.append({"type": "mapping", "kind": kind,
                              "anchor": {"row": 1 if layout == "unfamiliar-en" else 3,
                                         "sheet": None if layout == "unfamiliar-en" else "结算明细", "page": None},
                              "columns": {str(i): key for i, key in enumerate(keys, 1) if key in aliases}})
    if kind == "CI" and layout == "metadata":
        keys.remove("order_id")
        labels = keys.copy()
        prefix = [["Order reference", rows[0]["order_id"]], []]
        confirmations.append({"type": "field", "kind": kind, "field": "order_id", "value": rows[0]["order_id"],
                              "source": {"file": "CI.csv", "row": 1}})
    if layout == "reordered":
        keys.reverse()
        labels = keys.copy()
    records = [[row.get(key, "") for key in keys] for row in rows]
    if kind == "CI" and layout == "unfamiliar-zh" or kind == "PO" and layout in {"canonical", "reordered"}:
        book = Workbook()
        sheet = book.active
        sheet.title = "结算明细" if kind == "CI" else "Purchase"
        if kind == "CI":
            sheet.append(["独立构造模拟发票"])
            sheet.append([])
        sheet.append(labels)
        for record in records:
            sheet.append(record)
        book.save(folder / f"{kind}.xlsx")
    elif kind == "PL" and layout == "canonical":
        # Pipe-separated text PDF: an explicitly supported textual input layout.
        pdf = Canvas(str(folder / "PL.pdf"), pagesize=landscape(A3), invariant=1)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(20, 795, " | ".join(labels))
        for i, record in enumerate(records, 1):
            pdf.drawString(20, 795 - i * 18, " | ".join(record))
        pdf.save()
    else:
        with (folder / f"{kind}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, delimiter=";" if layout == "reordered" else ",").writerows([*prefix, labels, *records])


def generate(output=DEFAULT):
    if output.exists():
        raise ValueError("冻结目录已存在；禁止覆盖。新实验请使用新的版本目录。")
    output.mkdir(parents=True)
    rng = random.Random(2026112204)
    families = [
        ("canonical", "development-reference", ["normal", "quantity", "price", "weight"]),
        ("reordered", "development-reference", ["missing_weight", "amount", "order_conflict", "normal"]),
        ("unfamiliar-en", "holdout", ["quantity", "price", "amount", "missing_weight"]),
        ("unfamiliar-zh", "holdout", ["normal", "quantity", "weight", "price"]),
        ("metadata", "holdout", ["normal", "quantity", "weight", "missing_weight"]),
        ("manual-boundaries", "holdout", ["duplicate", "unit_unknown", "order_conflict", "missing_weight"]),
    ]
    cases = []
    for family, split, scenarios in families:
        for name in scenarios:
            index = len(cases) + 1
            case_id = f"{index:02d}-{family}-{name}"
            folder = output / case_id
            folder.mkdir()
            rows = values(index, rng)
            findings = scenario(rows, name)
            if family == "reordered" and name == "normal":
                extra = values(index + 100, rng)
                for kind in KINDS:
                    extra[kind][0]["order_id"] = rows[kind][0]["order_id"]
                    if kind == "CI":
                        extra[kind][0]["invoice_id"] = rows[kind][0]["invoice_id"]
                    rows[kind].extend(extra[kind])
                rows["CI"].reverse()
            actions = []
            for kind in KINDS:
                write_document(folder, kind, rows[kind], family, actions)
            dump(folder / "truth.json", {"documents": rows, "findings": findings,
                "source": "independently constructed synthetic; no real enterprise documents",
                "answer_method": "declared by construction, no production parser or rules imported"})
            dump(folder / "confirmation.json", actions)
            cases.append({"id": case_id, "family": family, "split": split, "scenario": name})
    manifest = {"version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "seed": 2026112204, "source": "synthetic_only", "cases": cases,
        "limits": ["Same developer authored fixtures; this is not independent human assessment.",
                   "Holdout labels freeze templates before measurement; no parser tuning permitted during this run.",
                   "No actual human times, real-company samples or billable API amounts are inferred."],
        "files": {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
        "engine": {name: digest(PROJECT / name) for name in ["core/parser.py", "core/pdf_columns.py",
            "core/normalizer.py", "core/audit_rules.py", "agent/mapping_agent.py", "agent/diagnostic_agent.py",
            "agent/usage_meter.py", "agent/main_agent.py", "agent/tools.py", "core/schemas.py",
            "core/reporter.py", "agent/prompts.py", "scripts/evaluate_independent.py",
            "scripts/generate_independent_validation.py"]}}
    dump(output / "manifest.json", manifest)
    print(f"Frozen {len(cases)} synthetic cases; 8 reference + 16 holdout. Source and engine hashes recorded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT)
    generate(parser.parse_args().output)
