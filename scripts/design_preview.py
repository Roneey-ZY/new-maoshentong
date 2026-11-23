"""Local visual QA with synthetic B only; no login bypass in the production app.

Run on loopback: streamlit run scripts/design_preview.py --server.address 127.0.0.1
Never reads Secrets, calls a model, connects to a database or accepts uploads.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from design_system import apply_theme, render_header, render_footer, enable_motion, login_heading
from agent.tools import ReviewRun, run_rules
from core.demo import DATA, load_case
from core.reporter import describe

st.set_page_config(page_title="贸审通 · 本地设计验收", layout="wide")
apply_theme()
if st.query_params.get("view") == "login":
    login_heading()
    with st.container(key="tc_login_panel"), st.form("design_login_preview"):
        st.text_input("账号")
        st.text_input("密码", type="password")
        st.form_submit_button("登录", type="primary")
    st.caption("仅本地界面预览，不执行账号验证；正式登录仍由app.py保护。")
    st.stop()

render_header(has_result=True)
st.html('<output id="tc-motion-check" style="font-size:13px;color:#586a73">本地动画验收：等待浏览器初始化</output>')
enable_motion()
with st.sidebar:
    st.caption("本地设计验收 · 合成案例 B")
    st.button("退出登录", disabled=True)
    st.divider()
    st.subheader("审核任务")
    st.selectbox("历史任务", ["模拟交易 · 数量与重量核对"])
    st.selectbox("历史版本", ["第1版"])
    st.button("打开所选版本")
st.header("01 / 准备单证", anchor="tc-prepare")
st.radio("单证来源", ["演示案例", "上传文件"], horizontal=True)
st.selectbox("选择演示案例", ["B · 四项典型异常"])
st.button("一键加载演示案例", type="primary")
st.header("02 / 执行审核", anchor="tc-run")
left, right = st.columns(2)
left.radio("执行模式", ["规则演示模式", "AI智能体模式"], horizontal=True)
right.radio("报告语言", ["中文", "English"], horizontal=True)
st.caption("本预览直接运行合成案例B的真实规则，不调用模型或读写任务。")
run = ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
run_rules(run)
st.header("03 / 核验结果", anchor="tc-results")
for column, label, value in zip(st.columns(4), ["业务差异", "逻辑矛盾", "无法核验", "已执行核验"], [*run.result.counts.values(), run.result.compared]):
    column.metric(label, value)
for finding in run.result.findings:
    title, explanation, advice = describe(finding, "zh")
    with st.expander(title):
        st.text(explanation)
        st.dataframe([{"字段": e.field, "值": e.value, "出处": e.source.display()} for e in finding.evidence], hide_index=True, width="stretch")
        st.text(advice)
st.download_button("下载Excel审核报告", run.report, "TradeCheck-design-preview.xlsx")
render_footer()
if st.query_params.get("panel") == "results":
    st.html('<script>requestAnimationFrame(() => document.getElementById("tc-results")?.scrollIntoView({block:"start"}));</script>', unsafe_allow_javascript=True)
