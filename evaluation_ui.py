"""Display published synthetic evaluation artifacts; never execute paid tests on page load."""
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import streamlit as st

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "data/independent_results/v1-live"
DATASET = ROOT / "data/independent_validation/v1"
NAMES = {"baseline": "原有解析与规则", "agent_test_confirmation": "Agent 建议＋程序模拟确认",
         "oracle_test_confirmation": "预设正确映射模拟人工确认"}


def percentage(value):
    return "—" if value is None else f"{value:.1%}"


def samples_zip():
    output = io.BytesIO()
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name in ["manifest.json", *manifest["files"]]:
            archive.writestr(name, (DATASET / name).read_bytes())
    return output.getvalue()


def evaluation_panel():
    with st.expander("评估记录 · 查看测试范围和失败案例"):
        path = RESULTS / "comparison.json"
        if not path.exists():
            st.info("评估记录尚未发布。完成离线实验后，可在这里查看结果和原始数据。")
            return
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("status") != "completed":
            st.info("本批实验尚未完成，暂不展示汇总成绩。")
            return
        st.caption("24 笔新构造模拟交易，含 8 笔开发参照、16 笔模板留出样本。不是企业实际单证或第三方测评。")
        st.caption("本页为首次冻结实验的历史结果（代码 f3f8161）。实验发现的少读商品行问题已加保护，"
                   "历史失败仍保留，不视为当前版本重新测试的成绩。")
        st.info("两个辅助组都由程序模拟确认。模型组采用真实模型建议；预设映射组采用事先写好的正确答案。"
                "没有测量真人操作时间，也不能据此推断任意单证的准确率。")
        split = st.selectbox("查看样本范围", ["holdout", "development-reference"],
            format_func=lambda value: "模板留出 · 16 笔" if value == "holdout" else "开发参照 · 8 笔")
        rows = []
        for mode, arm in result["arms"].items():
            if arm.get("skipped"):
                st.caption(f"{NAMES[mode]}：未执行，未计算成绩。")
                continue
            summary = arm["by_split"][split]
            rows.append({"处理方式": NAMES[mode], "完整通过": f"{summary['successful_cases']}/{summary['cases']}",
                "字段准确率": percentage(summary["field_accuracy"]),
                "异常精确率": percentage(summary["anomaly"]["precision"]),
                "异常召回率": percentage(summary["anomaly"]["recall"]),
                "待核验项 / 全部检查项": f"{summary['unverifiable_count']}/{summary['unverifiable_denominator']}",
                "平均程序耗时（秒）": round(summary["mean_ms"] / 1000, 3)})
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("完整通过要求字段、预期发现和报告均正确。平均程序耗时含模型请求和复审，不含真人确认时间。"
                   "待核验项应与原始记录一起判断，数量越少不一定越好。")
        available = [mode for mode, arm in result["arms"].items() if not arm.get("skipped")]
        mode = st.selectbox("查看处理方式", available, format_func=NAMES.get)
        arm = result["arms"][mode]
        selected_rows = [row for row in arm["records"] if row["split"] == split]
        failures_only = st.checkbox("只看未完整通过的案例", value=True)
        if failures_only:
            selected_rows = [row for row in selected_rows if not row["success"]]
        if selected_rows:
            index = st.selectbox("查看案例", range(len(selected_rows)), format_func=lambda i: selected_rows[i]["case"])
            record = selected_rows[index]
            st.text(f"字段差异 {len(record['field_differences'])} 项；异常漏报 {record['anomaly']['fn']} 项、"
                    f"误报 {record['anomaly']['fp']} 项；程序模拟确认 {record['simulated_confirmations']} 次。")
            if record["error"]:
                st.error(f"实验调用未完成：{record['error']}")
            st.json({key: record[key] for key in ["expected_findings", "actual_findings", "field_differences",
                     "confirmations", "diagnostic_rounds"]}, expanded=False)
        elif failures_only:
            st.caption("当前组与样本范围没有未完整通过的案例。可取消筛选查看全部记录。")
        summary = arm["summary"]
        st.caption(f"该组全批模型请求 {summary['model_attempts']} 次，失败请求 {summary['model_failed_attempts']} 次；"
                   f"已知输入 {summary['known_input_tokens']}、输出 {summary['known_output_tokens']} token。"
                   "未取得供应商账单，实际费用未统计。")
        st.download_button("下载实验说明", (RESULTS / "REPORT.md").read_bytes(), "TradeCheck-evaluation.md")
        st.download_button("下载逐笔实验记录", path.read_bytes(), "TradeCheck-comparison.json", mime="application/json")
        st.download_button("下载冻结模拟单证与标准答案", samples_zip(), "TradeCheck-frozen-samples.zip", mime="application/zip")
