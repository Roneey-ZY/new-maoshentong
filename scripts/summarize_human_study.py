"""Summarize actual observed sessions; never populate or simulate participants."""
import argparse
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path


def summarize(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    if not raw:
        raise ValueError("没有真人观察记录，不能计算真人指标。")
    rows, seen = [], set()
    for row in raw:
        participant, task = row["participant_id"].strip(), row["task_id"].strip()
        if not participant or task not in {"1", "2", "3"} or (participant, task) in seen:
            raise ValueError("参与者编号/任务编号缺失、非法或同一任务重复记录。")
        seen.add((participant, task))
        start, end = (datetime.fromisoformat(row[k]) for k in ("started_at", "finished_at"))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("开始与结束时间须含时区，且结束晚于开始。")
        if row["completed_without_help"] not in {"true", "false"}:
            raise ValueError("独立完成须按实际观察填true或false。")
        help_count, errors, satisfaction = (int(row[k]) for k in ("help_count", "error_count", "satisfaction_1_to_5"))
        complete = row["completed_without_help"] == "true"
        if min(help_count, errors) < 0 or not 1 <= satisfaction <= 5 or (complete and help_count):
            raise ValueError("帮助次数、错误次数、满意度或独立完成标记互相矛盾。")
        rows.append({"participant_id": participant, "task_id": task, "seconds": (end-start).total_seconds(),
                     "independent_success": complete, "help_count": help_count, "error_count": errors,
                     "satisfaction": satisfaction, "notes": row["notes"]})
    def aggregate(values):
        return {"observed_tasks": len(values), "independent_successes": sum(r["independent_success"] for r in values),
                "independent_success_rate": sum(r["independent_success"] for r in values)/len(values),
                "median_observed_seconds": statistics.median(r["seconds"] for r in values),
                "median_satisfaction": statistics.median(r["satisfaction"] for r in values)}
    return {"evidence_type": "user_supplied_actual_observations_not_independently_verified",
            "participants": len({r["participant_id"] for r in rows}), "overall": aggregate(rows),
            "by_task": {task: aggregate([r for r in rows if r["task_id"] == task]) for task in sorted({r["task_id"] for r in rows})},
            "records": rows, "note": "仅代表已记录的试用；不代表企业实证、随机样本或普遍准确率。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请使用新目录。")
    try:
        result = summarize(args.input)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True)
    (args.output / "human-study.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
