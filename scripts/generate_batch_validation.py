"""Declared synthetic batch examples; no audit engine is used to write answers."""
import copy
import csv
import json
from pathlib import Path

FIELDS = {
    "PO": "order_id,order_line_id,sku,quantity,unit,currency,unit_price".split(","),
    "CI": "invoice_id,invoice_line_id,order_id,order_line_id,sku,quantity,unit,currency,unit_price,line_amount".split(","),
    "PL": "packing_id,packing_line_id,invoice_id,invoice_line_id,order_id,order_line_id,sku,quantity,unit,net_kg,gross_kg".split(","),
}
ROOT = Path(__file__).resolve().parents[1] / "data/batch_demo/v1"


def generate(output=ROOT):
    if output.exists():
        raise ValueError("目录已存在，禁止覆盖原样本。")
    po = {"order_id": "BPO-001", "order_line_id": "1", "sku": "A-100", "quantity": "100", "unit": "PCS", "currency": "USD", "unit_price": "20"}
    ci, pl = [], []
    for index, qty in enumerate([60, 40], 1):
        ci.append({**po, "invoice_id": f"INV-{index}", "invoice_line_id": "1", "quantity": str(qty), "line_amount": str(qty * 20)})
        pl.append({k: ci[-1][k] for k in ["invoice_id", "invoice_line_id", "order_id", "order_line_id", "sku", "quantity", "unit"]})
        pl[-1].update(packing_id=f"PACK-{index}", packing_line_id="1", net_kg="30", gross_kg="35")
    base = {"PO": [po], "CI": ci, "PL": pl}
    truths = {
        "normal": {"difference": 0, "contradiction": 0, "review": 0, "info": 0},
        "partial": {"difference": 0, "contradiction": 0, "review": 0, "info": 1},
        "over": {"difference": 1, "contradiction": 1, "review": 0, "info": 0},
        "ambiguous": {"difference": 0, "contradiction": 0, "review": 6, "info": 0},
    }
    for name, expected in truths.items():
        rows = copy.deepcopy(base)
        if name == "partial":
            rows["CI"] = rows["CI"][:1]
            rows["PL"] = rows["PL"][:1]
        if name == "over":
            rows["CI"][1].update(quantity="50", line_amount="1000")
            rows["PL"][1].update(quantity="50", gross_kg="25")
        if name == "ambiguous":
            rows["CI"].append(copy.deepcopy(rows["CI"][0]))
        folder = output / name
        folder.mkdir(parents=True)
        for kind, fields in FIELDS.items():
            with (folder / f"{kind}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows[kind])
        (folder / "truth.json").write_text(json.dumps({"source": "project-authored synthetic", "expected_nonconsistent_counts": expected,
            "method": "declared_by_construction_not_from_audit_output"}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    generate()
