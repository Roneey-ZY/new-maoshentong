"""New labels/layouts frozen before evaluation. No production parser/rules imported."""
import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook

from scripts.generate_independent_validation import values, scenario, KEYS, dump, digest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data/optimization_validation/v2"


def generate(output=DEFAULT):
    if output.exists():
        raise ValueError("冻结目录已存在，禁止覆盖。")
    rng = random.Random(2026112302)
    cases = []
    families = {
        "new-en-v2": {"sku": "Seller product identifier", "quantity": "Pieces invoiced now", "unit_price": "Selling price for one unit", "line_amount": "Item subtotal on bill"},
        "new-zh-v2": {"sku": "供货商品代码", "quantity": "此票货品总件数", "unit_price": "每计量单位售价", "line_amount": "此商品结算小计"},
        "new-reference-v2": {},
    }
    for family, aliases in families.items():
        for name in ["normal", "quantity", "price", "weight"]:
            index = len(cases) + 1
            case_id = f"{index:02d}-{family}-{name}"
            folder = output / case_id
            folder.mkdir(parents=True)
            rows = values(600 + index, rng)
            findings = scenario(rows, name)
            actions = []
            for kind in KEYS:
                keys = list(reversed(KEYS[kind]))
                labels = [aliases.get(k, k) if kind == "CI" else k for k in keys]
                prefix = []
                if kind == "CI" and family == "new-reference-v2":
                    keys.remove("order_id")
                    labels = keys.copy()
                    prefix = [["Purchase order reference cited on bill", rows[kind][0]["order_id"]], []]
                    actions.append({"type": "field", "kind": "CI", "field": "order_id", "value": rows[kind][0]["order_id"], "source": {"file": "CI.csv", "row": 1}})
                elif kind == "CI":
                    actions.append({"type": "mapping", "kind": "CI", "anchor": {"row": 5 if family == "new-zh-v2" else 1, "sheet": "交易清单" if family == "new-zh-v2" else None, "page": None},
                                    "columns": {str(i): k for i, k in enumerate(keys, 1) if k in aliases}})
                content = [[row.get(k, "") for k in keys] for row in rows[kind]]
                if kind == "CI" and family == "new-zh-v2":
                    book = Workbook()
                    sheet = book.active
                    sheet.title = "交易清单"
                    for row in [["模拟交易清单"], ["本表仅供研究验证"], ["请核对原件"], [], labels, *content]:
                        sheet.append(row)
                    book.save(folder / "CI.xlsx")
                else:
                    with (folder / f"{kind}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                        csv.writer(stream, delimiter=";").writerows([*prefix, labels, *content])
            dump(folder / "truth.json", {"documents": rows, "findings": findings, "source": "project-authored synthetic; no real enterprise documents", "answer_method": "declared_by_construction"})
            dump(folder / "confirmation.json", actions)
            cases.append({"id": case_id, "family": family, "split": "holdout", "scenario": name})
    # Import hashes only after generation; this does not inspect app outputs or labels.
    from scripts.evaluate_optimization import engine_hashes
    engine = engine_hashes()
    engine["scripts/generate_optimization_holdout.py"] = digest(Path(__file__))
    dump(output / "manifest.json", {"version": 2, "source": "project_authored_synthetic_only", "seed": 2026112302,
        "created_utc": datetime.now(timezone.utc).isoformat(), "cases": cases,
        "limits": ["Same developer authored these fixtures; not third-party human evidence.", "New layout/name variants are not evidence of arbitrary-template generalization.", "Model confirmations are program simulation; actual human timing remains pending."],
        "files": {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}, "engine": engine})


if __name__ == "__main__":
    generate()
