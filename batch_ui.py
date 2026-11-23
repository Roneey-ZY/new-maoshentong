"""Batch CSV entry; deterministic mode is explicitly distinguished from AI mode."""
import hashlib
import io
import json
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile, ZIP_DEFLATED

import streamlit as st

from core.shipment_batch import FIELDS, SCOPE, read_batch_csv, audit_batch, make_batch_report

DEMO = Path(__file__).resolve().parent / "data/batch_demo/v1"
STATUS = {"consistent": "相符", "difference": "业务差异", "contradiction": "逻辑矛盾", "review": "无法核验", "info": "分批等业务状态"}


def batch_panel():
    with st.expander("分批交易核对 · 多张发票与装箱记录"):
        st.caption("本区按订单号＋采购行编号关联，再按发票号＋发票行编号关联装箱行。直接运行规则，无模型调用。")
        st.info(SCOPE)
        st.caption("第一版采用固定CSV模板，支持多订单、多发票和多装箱行；暂不将任意版式PDF自动拼成批次。此区结果暂不保存到任务历史，请下载留存。")
        archive = io.BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as z:
            for kind in FIELDS:
                z.writestr(f"{kind}.csv", ",".join(FIELDS[kind]) + "\n")
        st.download_button("下载分批交易CSV模板", archive.getvalue(), "TradeCheck-batch-template.zip")
        mode = st.radio("批次数据来源", ["内置分批演示", "上传批次CSV"], horizontal=True)
        files = []
        if mode == "内置分批演示":
            case = st.selectbox("选择分批案例", ["normal", "partial", "over", "ambiguous"], format_func=lambda key: {
                "normal": "两次开票、两次装箱，累计相符", "partial": "仅提供第一批，提示尚未齐全",
                "over": "累计超出采购量及装箱重量矛盾", "ambiguous": "重复记录，阻止静默累计"}[key])
            for kind in FIELDS:
                path = DEMO / case / f"{kind}.csv"
                files.append((kind, path.name, path.read_bytes()))
            st.caption("内置案例为本项目构造的模拟单证，不是真实企业交易。")
        else:
            for column, kind in zip(st.columns(3), FIELDS):
                uploaded = column.file_uploader({"PO": "采购行CSV", "CI": "发票行CSV", "PL": "装箱行CSV"}[kind], ["csv"], key=f"batch_upload_{kind}")
                if uploaded:
                    files.append((kind, uploaded.name, uploaded.getvalue()))
        digest = hashlib.sha256()
        for kind, name, data in files:
            digest.update(json.dumps([kind, name, len(data)], ensure_ascii=False).encode())
            digest.update(data)
        signature = digest.hexdigest()
        if st.button("核对分批交易", disabled=not files):
            started = perf_counter()
            st.session_state.pop("batch_record", None)
            try:
                records = [record for kind, name, data in files for record in read_batch_csv(kind, name, data)]
                result = audit_batch(records)
                report = make_batch_report(records, result)
                st.session_state["batch_record"] = {"signature": signature, "result": result, "report": report,
                                                      "ms": round((perf_counter() - started) * 1000, 2)}
            except ValueError as exc:
                st.error(str(exc))
        current = st.session_state.get("batch_record")
        if current and current["signature"] == signature:
            result = current["result"]
            st.caption(f"本次规则与报告生成耗时 {current['ms']} ms；模型调用0次。")
            st.dataframe([{"类别": STATUS[k], "项数": v} for k, v in result["counts"].items()], hide_index=True)
            if result["facts"]:
                st.dataframe(result["facts"], hide_index=True, width="stretch")
            for index, check in enumerate(result["checks"], 1):
                if check["status"] == "consistent":
                    continue
                with st.expander(f"{index} · {STATUS[check['status']]} · {check['rule']}"):
                    st.text(check["detail"])
                    st.dataframe(check["evidence"], hide_index=True, width="stretch")
            st.download_button("下载分批交易Excel报告", current["report"], "TradeCheck-batch.xlsx")
            st.download_button("下载分批交易核验JSON", json.dumps(result, ensure_ascii=False, indent=2), "TradeCheck-batch.json")
