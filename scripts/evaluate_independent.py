"""Three-arm evaluation on frozen synthetic templates; every confirmation is labelled.

No oracle answers are supplied to the model arm. Model proposals are applied by a
test driver (not a real person), without filtering proposals against ground truth.
"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import tomllib
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from agent.diagnostic_agent import apply_proposal, diagnose, preflight, revision_key
from agent.main_agent import configured_model
from agent.tools import ReviewRun, run_rules
from core.demo import load_case
from core.schemas import Source
from scripts.evaluate import score_run, summarize

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT = PROJECT / "data/independent_validation/v1"
MODES = ("baseline", "agent_test_confirmation", "oracle_test_confirmation")
LABELS = {"baseline": "原有解析与规则",
          "agent_test_confirmation": "真实 Agent 建议＋程序模拟确认",
          "oracle_test_confirmation": "预设正确映射＋程序模拟人工确认"}


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_freeze(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        if hash_file(folder / relative) != expected:
            raise ValueError(f"冻结样本校验失败：{relative}")
    for relative, expected in manifest["engine"].items():
        if hash_file(PROJECT / relative) != expected:
            raise ValueError(f"冻结后评估引擎已改变：{relative}。请使用新版本实验，不能覆盖原实验。")
    return manifest


def proposal_identity(proposal):
    # Descriptive text and source quotes do not affect duplicate-action detection.
    return json.dumps({key: value for key, value in proposal.items() if key not in {"labels", "quote"}}, sort_keys=True)


def apply_test_confirmations(run, diagnosis):
    """Explicit test-only simulation; does not endorse the model's semantic decisions.

    Rebase after each accepted proposal because applying any proposal changes the
    revision hash. Row counts are unchanged for the field/link proposals used here.
    Stale or invalid proposals are retained as failed actions, never silently fixed.
    """
    actions = []
    for proposal in diagnosis["proposals"]:
        started = perf_counter()
        try:
            rebased = {**diagnosis, "revision": revision_key(run), "proposals": [proposal], "log": []}
            revised = apply_proposal(run, rebased, 0)
            run = revised
            status, error = "applied_by_test_driver", None
        except Exception as exc:
            status, error = "rejected", type(exc).__name__
        actions.append({"proposal": proposal, "status": status, "error": error,
                        "ms": round((perf_counter() - started) * 1000, 3)})
    return run, actions


def oracle_diagnosis(run, steps):
    proposals = []
    for step in steps:
        value = copy.deepcopy(step)
        if value["type"] == "field":
            # Predeclared source line points to a synthetic source; no inferred value.
            value["source"] = Source(**value["source"]).model_dump()
        proposals.append(value)
    return {"revision": revision_key(run), "proposals": proposals, "log": []}


def run_case(folder, mode, live=None, max_rounds=2):
    truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    run = ReviewRun(load_case(folder), "zh", "rules")
    started = perf_counter()
    error, rounds, actions = None, [], []
    try:
        run_rules(run)
        initial_checks = run.result.compared
        if mode == "oracle_test_confirmation":
            steps = json.loads((folder / "confirmation.json").read_text(encoding="utf-8"))
            run, actions = apply_test_confirmations(run, oracle_diagnosis(run, steps))
        elif mode == "agent_test_confirmation":
            if live is None:
                raise ValueError("Real model credentials are required for the model arm")
            seen = set()
            for _ in range(max_rounds):
                if not preflight(run)["needs_help"]:
                    break
                provider, key, model = live
                diagnosis = diagnose(run, key, model, provider)
                rounds.append(diagnosis)
                if diagnosis["mode"] == "failed":
                    error = diagnosis["error"]
                    break
                fresh = [p for p in diagnosis["proposals"] if proposal_identity(p) not in seen]
                seen.update(proposal_identity(p) for p in fresh)
                if not fresh:
                    break
                # This arm never reads confirmation.json or expected field answers.
                run, attempted = apply_test_confirmations(run, {**diagnosis, "proposals": fresh})
                actions.extend(attempted)
                if not any(a["status"] == "applied_by_test_driver" for a in attempted):
                    break
    except Exception as exc:
        error = type(exc).__name__
        initial_checks = run.result.compared if run.result else 0
    elapsed = (perf_counter() - started) * 1000
    record = score_run(folder.name, truth, run, elapsed, error)
    requests = [request for result in rounds for request in result.get("model_requests", [])]
    complete = all(event["input_tokens"] is not None and event["output_tokens"] is not None for event in requests)
    reviews = run.result.counts["review"] if run.result else 0
    record.update(mode=mode, confirmation_method="none" if mode == "baseline" else "program_simulation_not_actual_human",
        confirmations=actions, diagnostic_rounds=rounds, real_human_confirmations=0,
        real_human_seconds=None, simulated_confirmations=sum(a["status"] == "applied_by_test_driver" for a in actions),
        checks_before=initial_checks, checks_after=run.result.compared if run.result else 0,
        unverifiable_count=reviews, unverifiable_denominator=reviews + (run.result.compared if run.result else 0),
        model_attempts=len(requests), model_failed_attempts=sum(r["status"] == "error" for r in requests),
        tokens_complete=complete, known_input_tokens=sum(r["input_tokens"] or 0 for r in requests),
        known_output_tokens=sum(r["output_tokens"] or 0 for r in requests),
        known_cached_input_tokens=sum(r["cached_input_tokens"] or 0 for r in requests),
        actual_billed_cost=None, cost_note="No billing ledger available; token usage is not actual billed cost.")
    return record, run.report


def aggregate(records):
    if not records:
        return {"cases": 0}
    summary = summarize(records)
    denominator = sum(row["unverifiable_denominator"] for row in records)
    summary.update(successful_cases=sum(row["success"] for row in records),
        field_correct=sum(r["field_correct"] for r in records), field_count=sum(r["field_count"] for r in records),
        unverifiable_count=sum(row["unverifiable_count"] for row in records),
        unverifiable_denominator=denominator,
        unverifiable_fraction=sum(row["unverifiable_count"] for row in records) / denominator if denominator else None,
        simulated_confirmations=sum(row["simulated_confirmations"] for row in records),
        real_human_confirmations=0, real_human_seconds=None,
        model_attempts=sum(row["model_attempts"] for row in records),
        model_failed_attempts=sum(row["model_failed_attempts"] for row in records),
        model_failed_cases=sum(bool(r["error"]) for r in records),
        tokens_complete=all(r["tokens_complete"] for r in records),
        known_input_tokens=sum(r["known_input_tokens"] for r in records),
        known_output_tokens=sum(r["known_output_tokens"] for r in records),
        known_cached_input_tokens=sum(r["known_cached_input_tokens"] for r in records),
        actual_billed_cost=None,
        failure_cases=[r["case"] for r in records if not r["success"]])
    return summary


def markdown_report(result):
    lines = ["# 冻结合成模板评估", "",
        "全部为独立构造的模拟单证；同一开发者设计样本，不是独立第三方或真实企业验证。",
        "开发参照 8 笔、模板留出 16 笔，结果按分组分别报告。冻结后未用本批结果调整解析和审核规则。",
        "Agent 组仅使用真实模型返回的、通过现有证据校验的建议；测试程序模拟逐条确认，不按标准答案筛选建议。",
        "预设映射组使用预先写好的正确对应关系模拟人工确认，代表该条件下的能力；没有实际人工用时数据。",
        "耗时包含程序解析、复审、报告生成及模型请求（若有），不含真人思考或点击时间。", "",
        "| 组别 | 范围 | 完整通过 | 字段准确率 | 异常精确率 | 异常召回率 | 待核验项/全部检查项 | 平均程序耗时 | 模拟确认 | 模型请求 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    fmt = lambda value: "不适用" if value is None else f"{value:.1%}"
    for mode, data in result["arms"].items():
        if data.get("skipped"):
            lines.append(f"\n{LABELS[mode]}：未执行，{data['skipped']}。")
            continue
        for split, summary in data["by_split"].items():
            lines.append(f"| {LABELS[mode]} | {split} | {summary['successful_cases']}/{summary['cases']} | "
                f"{fmt(summary['field_accuracy'])} | {fmt(summary['anomaly']['precision'])} | {fmt(summary['anomaly']['recall'])} | "
                f"{summary['unverifiable_count']}/{summary['unverifiable_denominator']} | {summary['mean_ms']/1000:.3f}s | "
                f"{summary['simulated_confirmations']} | {summary['model_attempts']} |")
    lines += ["", "## 模型消耗与失败记录", "",
        "以下是 API 返回的已知 token 数；失败请求没有返回消耗时标记为不完整，不计成零成本。没有供应商账单，实际收费留空。"]
    for mode, arm in result["arms"].items():
        if arm.get("skipped"):
            continue
        total = arm["summary"]
        lines.append(f"- {LABELS[mode]}：请求 {total['model_attempts']} 次，失败请求 {total['model_failed_attempts']}；"
            f"已知输入 {total['known_input_tokens']} token、输出 {total['known_output_tokens']} token，"
            f"缓存输入 {total['known_cached_input_tokens']} token；用量完整：{total['tokens_complete']}。")
        for row in arm["records"]:
            if not row["success"]:
                lines.append(f"  - {row['case']}：字段差异 {len(row['field_differences'])}，异常漏报 {row['anomaly']['fn']}、"
                    f"误报 {row['anomaly']['fp']}，复核漏报 {row['review']['fn']}、额外复核 {row['review']['fp']}，"
                    f"错误：{row['error'] or '无运行异常，但未满足完整任务标准'}。")
    lines += ["", "完整 JSON 包含每笔预期/实际发现、字段差异、工具事件、候选建议、模拟确认动作和请求消耗。",
              "不能把本批结果推广为所有企业单证的普遍准确率，也不能将程序模拟确认称为真实人工测试。", ""]
    return "\n".join(lines)


def evaluate(folder, output, live=None, max_rounds=2):
    manifest = verify_freeze(folder)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "comparison.json").exists():
        raise ValueError("结果目录已有实验记录，请使用新的输出目录保留原始结果。")
    result = {"schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": hash_file(folder / "manifest.json"), "frozen_engine": manifest["engine"],
        "source": manifest["source"], "human_measurement": "none; program_simulation_only",
        "environment": {"python": platform.python_version(), "platform": platform.system(),
            "requirements_sha256": hash_file(PROJECT / "requirements.txt"),
            "versions": {name: importlib.metadata.version(name) for name in ["openai-agents", "openai", "pydantic", "pdfplumber", "openpyxl"]}},
        "model": {"provider": live[0], "name": live[2]} if live else None,
        "max_diagnostic_rounds": max_rounds, "arms": {}}
    # Save partial progress after each transaction so failures and interruptions survive.
    def save_progress():
        (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for mode in MODES:
        if mode == "agent_test_confirmation" and live is None:
            result["arms"][mode] = {"skipped": "未配置真实模型调用，本组没有运行，不计算成绩"}
            save_progress()
            continue
        records = []
        result["arms"][mode] = {"records": records, "status": "running"}
        for item in manifest["cases"]:
            row, report = run_case(folder / item["id"], mode, live, max_rounds)
            row.update(split=item["split"], family=item["family"])
            records.append(row)
            if report:
                destination = output / "reports" / mode
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{item['id']}.xlsx").write_bytes(report)
            save_progress()
            print(f"{mode} {item['id']} success={row['success']} requests={row['model_attempts']}", flush=True)
        result["arms"][mode].update(status="completed", summary=aggregate(records),
            by_split={split: aggregate([row for row in records if row["split"] == split])
                      for split in ["development-reference", "holdout"]})
        save_progress()
    result["status"] = "completed"
    save_progress()
    (output / "REPORT.md").write_text(markdown_report(result), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-ai", action="store_true")
    parser.add_argument("--max-rounds", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    live = None
    if args.live_ai:
        path = PROJECT / ".streamlit/secrets.toml"
        settings = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        live = configured_model(lambda key, default="": os.environ.get(key) or str(settings.get(key, default)))
        if not live[1]:
            parser.error("未配置模型凭据；请在服务端配置，或者去掉 --live-ai。")
    evaluate(args.dataset, args.output, live, args.max_rounds)
