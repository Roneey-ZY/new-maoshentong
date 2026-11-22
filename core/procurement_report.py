import io
import json

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


def make_procurement_report(order, result, source):
    book = Workbook()
    overview = book.active
    overview.title = "范围与来源"
    for row in [
        ["项目", "内容"], ["采购订单", order["po_number"]], ["政策版本", result["policy"]],
        ["来源", source["source_page"]], ["来源版本", source["source_version"]],
        ["授权", source["license"]], ["数据性质", "第三方公开合成数据，非真实企业单证"],
        ["核对口径", "全量快照累计记录，不对应某张发票开具当日或某次收货"],
        ["单证区别", "收货记录不是装箱单"], ["模型调用", "0：本报告为确定性规则核对"],
        ["说明", "数量或价格差异需核对业务依据，不是欺诈或法律违规认定"],
    ]:
        overview.append(row)
    facts = book.create_sheet("采购行数量")
    facts.append(["采购行编号", "SKU", "单位", "采购数量", "累计收货数量", "累计开票数量", "收货记录数", "发票记录数"])
    for row in result["facts"]:
        facts.append([row[key] for key in ("line_id", "sku", "unit", "ordered_quantity", "received_quantity",
                                          "invoiced_quantity", "receipt_records", "invoice_records")])
    findings = book.create_sheet("核验明细")
    findings.append(["采购行", "SKU", "规则", "状态", "记录编号", "解释", "原始证据"])
    labels = {"consistent": "相符", "difference": "业务差异", "review": "无法核验或需人工复核", "info": "业务状态说明"}
    for row in result["checks"]:
        findings.append([row["line_id"], row["sku"], row["rule"], labels[row["status"]], row["record_id"],
                         row["detail"], json.dumps(row["evidence"], ensure_ascii=False)])
    scope = book.create_sheet("规则适用范围")
    scope.append(["规则", "适用性", "依据"])
    for row in result["applicability"]:
        scope.append([row["rule"], row["status"], row["basis"]])
    trace = book.create_sheet("转换溯源")
    trace.append(["对象", "目标字段", "原文件", "起始行", "结束行", "原记录编号", "原列", "原值", "转换方式"])

    def origins(name, row):
        for key, value in row.get("origins", {}).items():
            trace.append([name, key, value["file"], value["row"], value["end_row"], value["record_id"],
                          value["column"], value["raw"], value["method"]])

    origins("采购订单", order)
    for line in order["lines"]:
        origins("采购行 " + line["line_id"], line)
        for row in line["receipts"]:
            origins("收货 " + row["receipt_id"], row)
        for row in line["invoices"]:
            origins("发票 " + row["invoice_id"], row)
    for sheet in book:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="132B49")
            cell.font = Font(color="FFFFFF", bold=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 27
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
