import streamlit as st

from core.evidence import original_evidence, pdf_page_image
from core.reporter import FIELD_NAMES, describe
from core.review import finding_key


def evidence_panel(run):
    if not run.result.findings:
        return
    with st.expander("原文证据对照 · 查看差异来自哪里", expanded=False):
        selected = st.selectbox("选择证据对应的发现", run.result.findings,
                                format_func=lambda f: f"{f.id} · {describe(f, 'zh')[0]}", key="evidence_finding")
        st.caption("上传原件与结构化预览分别展示；预览修改不会覆盖原始文件。")
        key = finding_key(selected, run.files)
        if st.button("读取原始证据", key="read_evidence"):
            st.session_state["evidence_open"] = key
        if st.session_state.get("evidence_open") != key:
            return
        columns = st.columns(min(2, max(1, len(selected.evidence))))
        for index, evidence in enumerate(selected.evidence):
            with columns[index % len(columns)]:
                st.subheader(f"{evidence.kind} · {FIELD_NAMES.get(evidence.field, evidence.field)}")
                st.text(evidence.source.display())
                st.text(f"当前核验值：{evidence.value if evidence.value is not None else '缺失'}")
                if evidence.edited:
                    st.warning("该核验值来自预览修改，请与下方原件区分。")
                file = next((f for f in run.files if f.kind == evidence.kind and f.name == evidence.source.file), None)
                if file is None:
                    st.info("未找到对应原文件。")
                    continue
                try:
                    original = original_evidence(file, evidence.source)
                    st.caption(original["message"])
                    if "text" in original:
                        st.image(pdf_page_image(file, evidence.source.page))
                        st.text(original["text"] or "此页未提取到文字。")
                    else:
                        st.dataframe([{"原行号": row["row"], "对应行": "是" if row["target"] else "上下文",
                                       **{f"第 {i+1} 列": value for i, value in enumerate(row["values"])}}
                                      for row in original["rows"]], hide_index=True, width="stretch")
                except Exception as exc:
                    st.info(f"原件预览未完成（{type(exc).__name__}），请下载原文件对照。")
                st.download_button("下载该原始单证", file.data, file.name,
                                   key=f"source_download_{index}")
