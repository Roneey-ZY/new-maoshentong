"""JSON snapshots are validated, never unpickled or executed."""
import hashlib

from agent.tools import InputFile, ReviewRun, run_rules
from agent.main_agent import AgentSummary
from core.schemas import Document, AuditResult
from core.review import ReviewBook, Decision


def pack(record, book=None, baseline=None, diagnostics=None):
    run, summary, error, elapsed = record
    if error or run.result is None or run.documents is None:
        raise ValueError("请先完成一次审核，再保存任务。")
    book = book or ReviewBook()
    return {"schema": 1, "language": run.language, "mode": run.mode,
            "documents": [d.model_dump(mode="json") for d in run.documents],
            "result": run.result.model_dump(mode="json"), "log": run.log,
            "summary": summary.model_dump(mode="json") if summary else None,
            "elapsed": elapsed, "baseline": baseline.model_dump(mode="json") if baseline else None,
            "review": {"shipment": book.shipment,
                       "decisions": {k: v.model_dump(mode="json") for k, v in book.decisions.items()},
                       "events": book.events}, "diagnostics": diagnostics}


def unpack(files, snapshot, report):
    if snapshot.get("schema") != 1:
        raise ValueError("不支持的任务快照版本")
    for f in files:
        if hashlib.sha256(bytes(f["data"])).hexdigest() != f["sha256"]:
            raise ValueError("原文件校验失败，请联系管理员")
    source = [InputFile(f["kind"], f["name"], bytes(f["data"])) for f in files]
    run = ReviewRun(source, snapshot["language"], snapshot["mode"],
                    documents=[Document.model_validate(d) for d in snapshot["documents"]])
    # Recompute deterministic results and reject a corrupted snapshot; don't trust JSON claims.
    run_rules(run)
    if run.result != AuditResult.model_validate(snapshot["result"]):
        raise ValueError("保存结果与当前规则不同，请下载历史报告后另存并重新审核。")
    run.log = snapshot["log"]
    run.report = report
    review = snapshot["review"]
    book = ReviewBook(review["shipment"], {k: Decision.model_validate(v) for k, v in review["decisions"].items()}, review["events"])
    summary = AgentSummary.model_validate(snapshot["summary"]) if snapshot["summary"] else None
    baseline = AuditResult.model_validate(snapshot["baseline"]) if snapshot.get("baseline") else None
    return (run, summary, None, snapshot["elapsed"]), book, baseline, snapshot.get("diagnostics")
