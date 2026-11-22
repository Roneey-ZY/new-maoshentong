"""Read-only public synthetic procurement demo, separate from private trade tasks."""
import hashlib
import io
import json
from time import perf_counter
from zipfile import ZipFile, ZIP_DEFLATED

import streamlit as st

from core.procurement import DATASET, read_tables, convert_order, audit_order
from core.procurement_report import make_procurement_report

ROOT = DATASET.parents[3]
STATUS = {"consistent": "相符", "difference": "业务差异", "review": "无法核验或需复核", "info": "业务状态说明"}


def raw_zip():
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted((DATASET / "raw").glob("*.csv")):
            archive.writestr("raw/" + path.name, path.read_bytes())
        for name in ("source.json", "protocol.json"):
            archive.writestr(name, (DATASET / name).read_bytes())
    return output.getvalue()


def procurement_panel():
    with st.expander("公开采购数据验证 · 订单、收货与开票"):
        path = DATASET / "evaluation/evaluation.json"
        if not path.exists():
            st.info("公开采购数据验证尚未发布。发布后可在这里选择订单、核对来源和下载报告。")
            return
        st.caption("来源：Database Sample，CC0。8 张原始表、492 行数据，对应 36 张采购订单，全部为合成数据。")
        st.warning("这里的收货记录不是装箱单。只核对采购、累计收货和开票记录，不补造重量、包装件数或发票金额。")
        st.caption("本区直接执行确定性 Python 规则，不是模型自主工具调用，也不是企业实证。差异需要业务复核，不等于违规。")
        try:
            tables, source = read_tables()
            protocol_bytes = (DATASET / "protocol.json").read_bytes()
            protocol = json.loads(protocol_bytes)
            published = json.loads(path.read_text(encoding="utf-8"))
            for name, expected in protocol["engine"].items():
                if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                    st.error("当前采购规则与冻结协议不一致，请联系维护者重新验证本区数据。其他审核功能可继续使用。")
                    return
        except (ValueError, OSError, KeyError):
            st.error("公开数据验证暂时无法加载，请联系维护者检查文件版本。")
            return
        st.dataframe([{"范围": "开发订单（含5笔试接入）" if split == "development" else "留出订单",
                       "订单数": row["orders"], "与独立参考计算一致": row["reference_agreement_orders"],
                       "字段转换差异": row["conversion_field_differences"], "来源位置差异": row["origin_errors"]}
                      for split, row in published["by_split"].items()], hide_index=True, width="stretch")
        st.caption("此处统计字段转换和项目自定义规则的一致性。参考结果由另一个计算程序复核，不是发布方异常标注，也不是模型准确率。")
        scope = st.selectbox("采购数据范围", ["pilot", "development", "holdout"], format_func=lambda key: {
            "pilot": "试接入的5笔订单", "development": "开发组12笔订单", "holdout": "留出组24笔订单"}[key])
        ids = protocol[{"pilot": "pilot_order_ids", "development": "development_order_ids", "holdout": "holdout_order_ids"}[scope]]
        numbers = {row["id"]: row["po_number"] for row in tables["purchase_orders"]}
        selected = st.selectbox("选择采购订单", ids, format_func=lambda key: numbers[key])
        signature = hashlib.sha256(protocol_bytes).hexdigest()
        if st.button("执行本订单采购规则核对"):
            events = []
            def execute(function, *args):
                event = {"顺序": len(events) + 1, "函数": function.__name__, "状态": "执行中"}
                events.append(event)
                started = perf_counter()
                try:
                    value = function(*args)
                    event["状态"] = "成功"
                    return value
                except Exception:
                    event["状态"] = "失败"
                    raise
                finally:
                    event["耗时毫秒"] = round((perf_counter() - started) * 1000, 3)
            try:
                current_tables, current_source = execute(read_tables)
                order = execute(convert_order, current_tables, selected)
                result = execute(audit_order, order)
                report = execute(make_procurement_report, order, result, current_source)
                st.session_state["procurement_result"] = {"signature": signature, "id": selected, "order": order,
                    "result": result, "report": report, "events": events}
            except (ValueError, OSError):
                st.session_state.pop("procurement_result", None)
                st.error("本订单未完成核对，请联系维护者检查来源与关联。")
                st.dataframe(events, hide_index=True)
        state = st.session_state.get("procurement_result")
        if state and (state["id"], state["signature"]) == (selected, signature):
            result = state["result"]
            st.dataframe(state["events"], hide_index=True, width="stretch")
            st.caption("以上为按钮实际执行的规则函数记录，不表示大模型选择了这些函数。")
            st.text(f"业务差异 {result['counts']['difference']} 项；无法核验或需复核 {result['counts']['review']} 项；"
                    f"业务状态说明 {result['counts']['info']} 项。")
            st.dataframe([{"采购行": r["line_id"], "SKU": r["sku"], "单位": r["unit"],
                           "采购数量": r["ordered_quantity"], "累计收货": r["received_quantity"],
                           "累计开票": r["invoiced_quantity"], "收货记录数": r["receipt_records"], "发票记录数": r["invoice_records"]}
                          for r in result["facts"]], hide_index=True, width="stretch")
            st.caption("空白数量表示未提供记录，不能当作实际数量为零。累计数据不能说明某张发票开具当日的到货情况。")
            st.dataframe([{"采购行": r["line_id"], "SKU": r["sku"], "规则": r["rule"], "状态": STATUS[r["status"]],
                           "说明": r["detail"]} for r in result["checks"]], hide_index=True, width="stretch")
            st.json({"采购订单及字段来源": state["order"], "规则证据": result["checks"]}, expanded=False)
            st.download_button("下载采购核对 Excel", state["report"], f"procurement-{numbers[selected]}.xlsx")
            st.download_button("下载本订单转换与证据", json.dumps({"order": state["order"], "result": result},
                ensure_ascii=False, indent=2), f"procurement-{numbers[selected]}.json", mime="application/json")
        st.dataframe([{"规则": row["rule"], "适用性": row["status"], "依据": row["basis"]}
                      for row in published["applicability"]], hide_index=True, width="stretch")
        st.download_button("下载公开采购原始数据与分组协议", raw_zip(), "procurement-public-source.zip", mime="application/zip")
        st.download_button("下载公开采购验证结果", path.read_bytes(), "procurement-evaluation.json", mime="application/json")
        st.markdown(f"[查看原始发布页面]({source['source_page']})")
