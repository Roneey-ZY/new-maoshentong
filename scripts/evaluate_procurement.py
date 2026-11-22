"""Pilot five orders, freeze by-order splits, then evaluate the separate procurement policy."""
import argparse
import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from core.procurement import DATASET, POLICY, APPLICABILITY, read_tables, convert_order, audit_order
from core.procurement_report import make_procurement_report
from scripts.procurement_reference import raw_tables, expected, flatten

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ("core/procurement.py", "core/procurement_report.py", "scripts/procurement_reference.py", "scripts/evaluate_procurement.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def status_keys(checks):
    return Counter((row["line_id"], row["rule"], row["record_id"], row["status"]) for row in checks)


def scores(truth, actual, status):
    a = Counter({k: v for k, v in truth.items() if k[-1] == status})
    b = Counter({k: v for k, v in actual.items() if k[-1] == status})
    tp, fp, fn = sum((a & b).values()), sum((b - a).values()), sum((a - b).values())
    return {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def origins_in(order):
    yield from order["origins"].values()
    for line in order["lines"]:
        yield from line["origins"].values()
        for row in [*line["receipts"], *line["invoices"]]:
            yield from row["origins"].values()


def check_origins(order, tables):
    lookup = {(r["_source"]["file"], r["_source"]["row"]): r for rows in tables.values() for r in rows}
    result = []
    for origin in origins_in(order):
        row = lookup.get((origin["file"], origin["row"]))
        if row is None or row.get(origin["column"]) != origin["raw"] or row["id"] != origin["record_id"]:
            result.append(origin)
    return result


def measure_order(folder, tables, source, reference, order_id, split, output, audit_fn=audit_order):
    started = perf_counter()
    order = convert_order(tables, order_id)
    result = audit_fn(order)
    expected_fields, expected_keys = expected(reference, order_id)
    actual_fields = flatten(order)
    differences = [{"field": key, "expected": expected_fields.get(key), "actual": actual_fields.get(key)}
                   for key in sorted(expected_fields.keys() | actual_fields.keys())
                   if expected_fields.get(key) != actual_fields.get(key)]
    trace_errors = check_origins(order, tables)
    actual_keys = status_keys(result["checks"])
    missing, extra = list((expected_keys - actual_keys).elements()), list((actual_keys - expected_keys).elements())
    report = make_procurement_report(order, result, source)
    elapsed = round((perf_counter() - started) * 1000, 3)
    record = {"order_id": str(order_id), "po_number": order["po_number"], "split": split,
        "reference_agreement": not differences and not missing and not extra and not trace_errors,
        "converted_fields": len(expected_fields.keys() | actual_fields.keys()), "field_differences": differences,
        "origins_checked": len(list(origins_in(order))), "origin_errors": trace_errors,
        "expected_keys": list(expected_keys.elements()), "actual_keys": list(actual_keys.elements()),
        "missing_checks": missing, "extra_checks": extra,
        "difference": scores(expected_keys, actual_keys, "difference"),
        "review": scores(expected_keys, actual_keys, "review"),
        "counts": result["counts"], "elapsed_ms": elapsed, "model_calls": 0,
        "real_human_checks": 0, "ground_truth_source": "project_declared_snapshot_policy_independent_Fraction_reference_not_provider_labels"}
    write(output / "orders" / f"{order_id}.json", {"converted": order, "result": result, "evaluation": record})
    (output / "reports").mkdir(parents=True, exist_ok=True)
    (output / "reports" / f"{order_id}.xlsx").write_bytes(report)
    return record


def summary(records):
    return {"orders": len(records), "reference_agreement_orders": sum(r["reference_agreement"] for r in records),
        "converted_fields_checked": sum(r["converted_fields"] for r in records),
        "conversion_field_differences": sum(len(r["field_differences"]) for r in records),
        "origins_checked": sum(r["origins_checked"] for r in records),
        "origin_errors": sum(len(r["origin_errors"]) for r in records),
        "counts": {status: sum(row["counts"][status] for row in records) for status in ("consistent", "difference", "review", "info")},
        "missing_reference_checks": sum(len(r["missing_checks"]) for r in records),
        "extra_checks": sum(len(r["extra_checks"]) for r in records),
        "mean_ms": round(statistics.mean(r["elapsed_ms"] for r in records), 3) if records else None,
        "model_calls": 0, "real_human_checks": 0,
        "failed_orders": [r["po_number"] for r in records if not r["reference_agreement"]]}


def run_orders(folder, output, selections, phase, audit_fn=audit_order):
    if output.exists():
        raise ValueError("输出目录已存在，请使用新目录保留原记录。")
    tables, source = read_tables(folder)
    reference = raw_tables(folder)
    output.mkdir(parents=True)
    records = []
    for order_id, split in selections:
        records.append(measure_order(folder, tables, source, reference, order_id, split, output, audit_fn))
    result = {"schema": 1, "phase": phase, "created_utc": datetime.now(timezone.utc).isoformat(),
        "policy": POLICY, "source_sha256": sha(folder / "source.json"), "data_nature": source["data_nature"],
        "engine": {name: sha(ROOT / name) for name in ENGINE},
        "evaluation_scope": "field_conversion_and_own_policy_reference_conformance_not_model_or_real_document_accuracy",
        "summary": summary(records), "by_split": {split: summary([r for r in records if r["split"] == split])
            for split in dict.fromkeys(dict(selections).values())}, "records": records, "applicability": APPLICABILITY}
    write(output / "evaluation.json", result)
    lines = ["# 第三方公开合成采购数据验证", "",
        "来源：Database Sample，CC0-1.0，8 表 492 行，共 36 张采购订单。没有使用付费数据。",
        "收货记录不是装箱单。本页是 CSV 字段转换与项目自定义采购快照规则的验证，不是模型准确率或企业实证。",
        "参考答案由独立 Fraction 计算实现按项目口径计算，并非发布方提供的官方异常标注。", "",
        "| 范围 | 订单数 | 与独立参考计算一致 | 转换字段核对 | 字段差异 | 溯源差异 | 业务差异项 | 人工复核项 | 状态说明项 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for split, values in result["by_split"].items():
        lines.append(f"| {split} | {values['orders']} | {values['reference_agreement_orders']} | "
            f"{values['converted_fields_checked']} | {values['conversion_field_differences']} | {values['origin_errors']} | "
            f"{values['counts']['difference']} | {values['counts']['review']} | {values['counts']['info']} |")
    lines += ["", "## 口径与限制", "",
        "- 全量快照分别累计同一采购行的收货与开票，不把发票强行配给某一次收货。",
        "- 无收货或发票记录保留无法核验，不把缺失当作实际数量零。少于订单可以是分批，作为状态说明。",
        "- 多张不同编号发票不直接判重复；累计超过订单或收货时给出业务核对提醒，不认定违法或欺诈。",
        "- 不提供逐单币种、金额、合格验收数量和重量时，不补造字段，不计算这些能力的通过率。",
        "- 同一订单所有采购行、收货、发票均在同一分组。5 笔试接入属于开发数据，不算独立留出。",
        "- 原始 CSV 与转换 JSON、字段来源和 Excel 报告均保留；没有进行付费模型调用或真人复核。", "",
        "未通过的订单：" + ("、".join(result["summary"]["failed_orders"]) or "本轮没有与项目参考口径不一致的订单。"), ""]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return result


def pilot(folder=DATASET, output=None):
    tables, _ = read_tables(folder)
    ids = sorted((row["id"] for row in tables["purchase_orders"]), key=int)[:5]
    return run_orders(folder, output or folder / "pilot", [(i, "pilot-development") for i in ids], "five_order_pilot")


def freeze(folder=DATASET):
    path = folder / "protocol.json"
    if path.exists():
        raise ValueError("分组协议已冻结，禁止覆盖。")
    pilot_result = json.loads((folder / "pilot/evaluation.json").read_text(encoding="utf-8"))
    if pilot_result["summary"]["reference_agreement_orders"] != 5 or pilot_result["source_sha256"] != sha(folder / "source.json"):
        raise ValueError("先完成同一来源版本的 5 笔试接入。")
    tables, _ = read_tables(folder)
    ids = sorted((row["id"] for row in tables["purchase_orders"]), key=int)
    pilots = [r["order_id"] for r in pilot_result["records"]]
    remaining = sorted(set(ids) - set(pilots), key=lambda value: hashlib.sha256(("procurement-split-20261122/" + value).encode()).hexdigest())
    development = pilots + remaining[:7]
    holdout = remaining[7:]
    protocol = {"schema": 1, "frozen_utc": datetime.now(timezone.utc).isoformat(), "policy": POLICY,
        "pilot_order_ids": pilots, "development_order_ids": development, "holdout_order_ids": holdout,
        "split_unit": "purchase_order_id_all_children_together", "source_sha256": sha(folder / "source.json"),
        "engine": {name: sha(ROOT / name) for name in ENGINE},
        "warning": "External synthetic business records, not external labels or real enterprise evidence; no LLM accuracy claim."}
    write(path, protocol)
    print(f"Frozen {len(development)} development (including 5 pilots) + {len(holdout)} holdout orders")
    return protocol


def evaluate(folder=DATASET, output=None):
    protocol = json.loads((folder / "protocol.json").read_text(encoding="utf-8"))
    if sha(folder / "source.json") != protocol["source_sha256"]:
        raise ValueError("冻结来源记录已改变")
    for name, expected_hash in protocol["engine"].items():
        if sha(ROOT / name) != expected_hash:
            raise ValueError(f"冻结规则已改变：{name}，请归档旧实验后创建新版本。")
    development, holdout = protocol["development_order_ids"], protocol["holdout_order_ids"]
    if set(development) & set(holdout):
        raise ValueError("同一订单出现在两个分组中")
    tables, _ = read_tables(folder)
    if set(development + holdout) != {row["id"] for row in tables["purchase_orders"]}:
        raise ValueError("分组未覆盖全部订单")
    return run_orders(folder, output or folder / "evaluation", [(i, "development") for i in development]
                      + [(i, "holdout") for i in holdout], "frozen_external_synthetic_evaluation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["pilot", "freeze", "evaluate"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.phase == "pilot":
        pilot(output=args.output)
    elif args.phase == "freeze":
        freeze()
    else:
        evaluate(output=args.output)
