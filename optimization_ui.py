"""Read-only display of versioned trials; never starts paid model evaluation."""
import json
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent / "data/optimization_results"
NAMES = {"baseline": "原解析与规则", "agent_test_confirmation": "真实模型建议＋程序模拟确认",
         "oracle_test_confirmation": "预设正确映射模拟人工确认"}


def optimization_panel():
    with st.expander("本轮优化验证 · 旧失败复测与新模板"):
        st.info("以下模型建议由程序模拟确认，没有真人操作时间。旧失败复测与新模板分开统计；样本均为模拟数据。")
        for directory, title in [("regression-v2", "7个旧失败案例复测"), ("holdout-v2", "12个新合成模板留出")]:
            path = ROOT / directory / "comparison.json"
            if not path.exists():
                st.caption(title + "：尚无运行记录。")
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("status") != "completed":
                st.caption(title + "：试验未完整结束，不展示汇总成绩。")
                continue
            st.markdown("**" + title + "**")
            rows = []
            for mode, arm in result["arms"].items():
                if arm.get("skipped"):
                    continue
                summary = arm["summary"]
                rows.append({"处理方式": NAMES[mode], "完整通过": f"{summary['successful_cases']}/{summary['cases']}",
                    "字段准确率": f"{summary['field_accuracy']:.1%}", "异常精确率": summary["anomaly"]["precision"],
                    "异常召回率": summary["anomaly"]["recall"], "平均程序耗时（秒）": round(summary["mean_ms"]/1000, 3),
                    "模型请求次数": summary["model_attempts"], "未完成调用的案例数": summary["model_failed_cases"]})
            st.dataframe(rows, hide_index=True, width="stretch")
            failures = [r for r in result["arms"].get("agent_test_confirmation", {}).get("records", []) if not r["success"]]
            if failures:
                st.caption("模型组未完整通过：" + "、".join(r["case"] for r in failures))
                with st.expander("查看保留的失败细节 · " + directory):
                    st.json(failures, expanded=False)
            st.download_button("下载逐笔记录 · " + title, path.read_bytes(), "TradeCheck-" + directory + ".json")
        st.caption("模型模式包含网络等待，并不比规则模式更快；当前价值在工具调度、字段建议和解释。程序耗时不含真人确认。实际收费未取得账单，未统计。")
        st.caption("首次失败与中断试验保留在源码data/optimization_results的v1目录；中断批次不能作为完整成绩。")
