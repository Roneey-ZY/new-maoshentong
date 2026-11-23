import streamlit as st

from agent.diagnostic_agent import diagnose, apply_proposal, preflight, revision_key
from agent.main_agent import AgentRunError
from core.diagnostic_presenter import proposal_card, preview_impact


def diagnostic_panel(run, key, model, provider):
    with st.expander("疑难处理 Agent · 检查未识别字段和商品关联"):
        issue = preflight(run)
        if not issue["needs_help"]:
            st.info("当前关键字段可核验，无需额外模型调用。已有业务差异仍需核对。")
            return None
        st.caption("模型可选择检查表头、缺失字段原文或商品关联。所有建议均需你逐项确认，不自动改数值。")
        consent = st.checkbox("允许本次疑难处理发送候选表头、有限原文及商品名称和单位给模型", key="diagnosis_consent")
        st.caption("只在需要时发送工具选取的有限内容，可能含业务信息；请先确认适合发送。原文件不整份上传给模型。")
        if st.button("让 Agent 检查疑难项", disabled=not consent or not key):
            try:
                with st.spinner("Agent 正在选择检查工具"):
                    st.session_state["diagnostics"] = diagnose(run, key, model, provider)
            except AgentRunError as exc:
                st.error(str(exc))
        diagnosis = st.session_state.get("diagnostics")
        if not diagnosis or diagnosis["revision"] != revision_key(run):
            return None
        st.text(diagnosis["summary"])
        if diagnosis.get("error"):
            st.error(diagnosis["error"])
        if diagnosis.get("mode") == "unwrapped_output":
            st.caption("模型返回了JSON代码框，已在本地去掉外层格式并通过结构校验，没有额外请求模型。")
        elif diagnosis.get("mode") == "repaired_output":
            st.info("模型首次输出的格式有误，已完成一次格式重试。以下仍是待确认建议。")
        elif diagnosis.get("mode") == "partial_output":
            st.warning("模型未返回完整有效结果。这里只保留能单独校验的候选，其他疑难项仍需人工检查。")
        if diagnosis.get("output_events"):
            with st.expander("查看输出校验与格式重试记录"):
                st.json(diagnosis["output_events"])
        if diagnosis["log"]:
            st.dataframe(diagnosis["log"], hide_index=True, width="stretch")
        for index, proposal in enumerate(diagnosis["proposals"]):
            st.markdown(f"**候选建议 {index+1}：{'表头对应' if proposal['type']=='mapping' else '缺失字段' if proposal['type']=='field' else '商品关联'}**")
            try:
                card = proposal_card(run, proposal)
                st.text(card["problem"])
                st.caption("原始出处：" + card["source"])
                if card.get("quote"):
                    st.text("原文：" + card["quote"])
                st.dataframe(card["rows"], hide_index=True, width="stretch")
                st.text("确认后的影响：" + card["impact"])
                if st.button("预览复审结果", key=f"preview_diagnosis_{diagnosis['revision']}_{index}"):
                    st.dataframe(preview_impact(run, diagnosis, index), hide_index=True, width="stretch")
                    st.caption("这是本地规则的试算，尚未应用或保存。待核验项减少不代表业务差异已经解决。")
            except (ValueError, KeyError, StopIteration, IndexError):
                st.warning("这条建议暂时无法安全预览，请重新获取建议或手动检查原件。")
                continue
            with st.expander("查看建议的技术明细"):
                st.json(proposal)
            confirmed = st.checkbox("我已对照原件确认这条建议", key=f"confirm_diagnosis_{diagnosis['revision']}_{index}")
            if st.button("应用此建议并复审", key=f"apply_diagnosis_{index}", disabled=not confirmed):
                try:
                    return apply_proposal(run, diagnosis, index)
                except ValueError as exc:
                    st.error(str(exc))
        if not diagnosis["proposals"]:
            st.info("没有通过证据校验的可应用建议。请继续人工检查，不会虚构字段。")
    return None
