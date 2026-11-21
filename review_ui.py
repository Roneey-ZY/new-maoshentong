import streamlit as st

from auth import setting
from core.review import REASONS, STATUSES, ReviewBook
from core.reporter import describe


def review_panel(run):
    book = st.session_state.setdefault("review_book", ReviewBook())
    reviewer = setting("LOGIN_USERNAME")
    st.caption("记录业务原因不会删除原始差异；修改数值后须重新运行规则。复核记录仅保存在当前登录会话。")
    shipment_labels = {"unknown": "尚未确认", "whole": "本次为整单交付", "partial": "本次为分批交付"}
    with st.form("shipment_context"):
        shipment = st.selectbox("本次交付范围", list(shipment_labels),
                                 index=list(shipment_labels).index(book.shipment), format_func=shipment_labels.get)
        if st.form_submit_button("保存交付范围"):
            book.set_shipment(shipment, reviewer)
            st.success("已保存；数量差异仍保留，需逐项核对。")
    if run.result.findings:
        finding = st.selectbox("选择待处理发现", run.result.findings,
                                format_func=lambda f: f"{f.id} · {describe(f, 'zh')[0]}", key="review_finding")
        previous = book.current(finding, run.files)
        allowed = ["pending", "correction_required"]
        if finding.category == "difference":
            allowed.insert(1, "explained")
        with st.form(f"decision_{finding.id}"):
            status = st.selectbox("复核状态", allowed,
                                  index=allowed.index(previous.status) if previous and previous.status in allowed else 0,
                                  format_func=lambda s: STATUSES[s][0])
            reason = st.selectbox("业务原因", list(REASONS),
                                  index=list(REASONS).index(previous.reason) if previous else 0,
                                  format_func=lambda s: REASONS[s][0])
            note = st.text_area("复核说明及依据", value=previous.note if previous else "", max_chars=1000,
                                placeholder="例如：客户邮件确认本批先发 90 件，剩余 10 件待交付；请对照邮件及装箱记录。")
            if st.form_submit_button("保存复核记录"):
                try:
                    book.record(finding, run.files, status, reason, note, reviewer)
                    st.success("已记录。原始差异和规则结论保持不变。")
                except ValueError as exc:
                    st.error(str(exc))
        st.dataframe([{"发现": f.id, "核验项": describe(f, "zh")[0], "状态": STATUSES[
            book.current(f, run.files).status if book.current(f, run.files) else "pending"][0]}
            for f in run.result.findings], hide_index=True, width="stretch")
    if book.events:
        with st.expander("本次会话的复核操作记录"):
            st.json(book.events)
    return book
