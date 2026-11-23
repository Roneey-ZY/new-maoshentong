"""Versioned optimization trials: old failures are regression, never new holdout."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.evaluate_independent import run_case, aggregate, hash_file, verify_freeze

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ["agent/diagnostic_agent.py", "agent/diagnostic_output.py", "agent/mapping_agent.py",
          "agent/usage_meter.py", "agent/main_agent.py", "agent/tools.py", "core/parser.py",
          "core/pdf_columns.py", "core/normalizer.py", "core/audit_rules.py", "core/schemas.py",
          "core/reporter.py", "agent/prompts.py", "scripts/evaluate.py",
          "scripts/evaluate_independent.py", "scripts/evaluate_optimization.py"]
OLD_FAILURES = ["09-unfamiliar-en-quantity", "10-unfamiliar-en-price", "14-unfamiliar-zh-quantity",
                "18-metadata-quantity", "20-metadata-missing_weight", "21-manual-boundaries-duplicate",
                "22-manual-boundaries-unit_unknown"]


def engine_hashes():
    return {name: hash_file(ROOT / name) for name in ENGINE}


def evaluate(dataset, output, live=None, regression=False):
    if output.exists():
        raise ValueError("结果目录已存在，请用新的目录保留所有试验记录。")
    if regression:
        manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            if hash_file(dataset / name) != digest:
                raise ValueError("旧样本发生变化，不能复测。")
        cases = [item for item in manifest["cases"] if item["id"] in OLD_FAILURES]
        if len(cases) != len(OLD_FAILURES):
            raise ValueError("旧失败案例不齐全。")
    else:
        manifest = verify_freeze(dataset)
        cases = manifest["cases"]
    output.mkdir(parents=True)
    result = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
        "experiment_type": "known_failure_regression" if regression else "new_synthetic_template_holdout",
        "dataset_manifest_sha256": hash_file(dataset / "manifest.json"), "engine": engine_hashes(),
        "human_measurement": "none_program_simulation_only", "actual_billed_cost": None,
        "model": {"provider": live[0], "name": live[2]} if live else None, "arms": {}}
    def save():
        (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for mode in ["baseline", "agent_test_confirmation", "oracle_test_confirmation"]:
        if mode == "agent_test_confirmation" and live is None:
            result["arms"][mode] = {"skipped": "No live model credentials; no model score."}
            continue
        rows = []
        result["arms"][mode] = {"status": "running", "records": rows}
        for item in cases:
            row, report = run_case(dataset / item["id"], mode, live)
            row.update(split="regression" if regression else item["split"], family=item["family"])
            events = [e for round in row["diagnostic_rounds"] for e in round.get("output_events", [])]
            row["format_validation_failures"] = sum(e["phase"] == "output_validation" for e in events)
            row["format_retry_attempts"] = sum(e["phase"] == "format_repair" for e in events)
            row["partial_rounds"] = sum(r.get("mode") == "partial_output" for r in row["diagnostic_rounds"])
            rows.append(row)
            if report:
                folder = output / "reports" / mode
                folder.mkdir(parents=True, exist_ok=True)
                (folder / (item["id"] + ".xlsx")).write_bytes(report)
            save()
            print(f"{mode} {item['id']} success={row['success']} requests={row['model_attempts']} format_failures={row['format_validation_failures']}", flush=True)
        result["arms"][mode].update(status="completed", summary=aggregate(rows))
        save()
    if result["engine"] != engine_hashes():
        raise ValueError("试验期间引擎发生变化，保留记录但不认定为完成。")
    result["status"] = "completed"
    save()
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--regression", action="store_true")
    p.add_argument("--live-ai", action="store_true")
    args = p.parse_args()
    live = None
    if args.live_ai:
        from agent.main_agent import configured_model
        live = configured_model(lambda key, default="": os.environ.get(key, default))
        if not live[1]:
            p.error("请在服务端设置模型环境变量；不会从前端请求评委提供Key。")
    evaluate(args.dataset, args.output, live, args.regression)
