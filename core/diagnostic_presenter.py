"""Local evidence cards; model prose is never the authority for a field value."""
from core.parser import TableAnchor, inspect_tables
from core.reporter import FIELD_NAMES
from core.schemas import Source

TYPES = {"PO": "采购订单", "CI": "商业发票", "PL": "装箱单"}


def proposal_card(run, proposal):
    kind = proposal.get("kind")
    if proposal["type"] == "mapping":
        file = next(f for f in run.files if f.kind == kind)
        anchor = TableAnchor(**proposal["anchor"])
        table = next(t for t in inspect_tables(file.name, file.data) if t.anchor == anchor)
        rows = []
        for column, field in proposal["columns"].items():
            index = int(column) - 1
            rows.append({"原表第几列": index + 1, "原表列名": table.labels[index],
                "建议识别为": FIELD_NAMES.get(field, field),
                "原始样例": " / ".join(values[index] for _, values in table.samples[:3] if index < len(values))})
        return {"problem": f"{TYPES[kind]}的部分列名没有识别出来。", "rows": rows,
            "source": anchor.source(file.name).display(),
            "impact": "确认后按这些列重新读取整张单证，再执行规则。商品行数和全部差异仍需复核；原文件保持不变。"}
    if proposal["type"] == "field":
        return {"problem": f"{TYPES[kind]}缺少可核验的{FIELD_NAMES[proposal['field']]}。",
            "source": Source(**proposal["source"]).display(), "quote": proposal["quote"],
            "rows": [{"字段": FIELD_NAMES[proposal["field"]], "当前值": "未识别",
                      "建议填入": proposal["value"]}],
            "impact": "确认后只在预览中补入这个字段，再检查单证关联或币种。原文出现该值并不代表它一定属于这个字段，请核对上下文。"}
    left, right = proposal["left"], proposal["right"]
    rows, sources = [], []
    for label, item in (("作为参照", left), ("准备修改", right)):
        doc = next(d for d in run.documents if d.kind == item["kind"])
        raw = doc.items[item["index"]].fields
        sources.append(raw["sku"].source.display())
        rows.append({"作用": label, "单证": TYPES[item["kind"]], "商品编码": item["sku"],
                     "商品名称": item["product"], "单位": item["unit"]})
    return {"problem": "两份单证的商品编码不同，但名称和单位相同，可能是同一商品。",
        "source": "；".join(sources), "rows": rows,
        "impact": f"确认后将{TYPES[right['kind']]}当前行编码 {right['sku']} 改为 {left['sku']}，再复审。名称相同也可能是不同规格，请先核对合同和实物。"}


def preview_impact(run, diagnosis, index):
    from agent.diagnostic_agent import apply_proposal
    revised = apply_proposal(run, diagnosis, index)
    labels = {"difference": "业务差异", "contradiction": "逻辑矛盾", "review": "无法核验"}
    rows = [{"类别": label, "修改前": run.result.counts[key], "预览修改后": revised.result.counts[key]}
            for key, label in labels.items()]
    rows.append({"类别": "已执行核验", "修改前": run.result.compared, "预览修改后": revised.result.compared})
    return rows
