import streamlit as st
import time

from auth import get_store
from core.storage import AccessDenied, Conflict, DatabaseUnavailable
from core.task_snapshot import pack, unpack
from core.reporter import make_report
from agent.tools import MODES


def _clear_work():
    for key in list(st.session_state):
        if key not in {"_db_login", "_login"}:
            del st.session_state[key]


def task_sidebar():
    store = get_store()
    if store is None:
        with st.sidebar:
            st.info("尚未配置外部任务存储：当前为会话模式，退出后数据不保留。管理员需配置 DATABASE_URL。")
        return None, None
    user = st.session_state["_db_login"]
    actor = user["id"]
    with st.sidebar:
        st.subheader("审核任务")
        if store.local:
            st.caption("本地 SQLite 开发模式；云端需使用外部 PostgreSQL。")
        if st.button("新建任务"):
            _clear_work()
            st.rerun()
        tasks = store.list_tasks(actor)
        if tasks:
            task_id = st.selectbox("历史任务", [t["id"] for t in tasks],
                                   format_func=lambda i: next(t["title"] for t in tasks if t["id"] == i))
            versions = store.versions(actor, task_id)
            version = st.selectbox("历史版本", [v["version"] for v in versions], format_func=lambda n: f"第 {n} 版")
            if st.button("打开所选版本"):
                task, files, snapshot, report = store.load(actor, task_id, version)
                record, book, baseline, diagnostics = unpack(files, snapshot, report)
                _clear_work()
                st.session_state.update(record=record, review_book=book, revision=version,
                                        task_id=task_id, task_version=version, task_title=task["title"],
                                        task_owner=task["owner_id"], diagnostics=diagnostics)
                if baseline:
                    st.session_state["baseline"] = baseline
                st.rerun()
            download_key = (task_id, version)
            if st.button("准备所选历史版本报告"):
                _, _, _, report = store.load(actor, task_id, version)
                st.session_state["history_download"] = (download_key, report)
            prepared = st.session_state.get("history_download")
            if prepared and prepared[0] == download_key:
                # Revalidate ownership on every render, including after account changes.
                store.versions(actor, task_id)
                st.download_button("保存历史 Excel", prepared[1], f"TradeCheck-v{version}.xlsx")
        if user["role"] == "admin":
            with st.expander("账号管理"):
                st.caption("新账号仅具有审核员权限。管理员可只读查看其他人的已保存任务。")
                with st.form("create_account", clear_on_submit=True):
                    username = st.text_input("新账号", max_chars=128)
                    password = st.text_input("初始密码（至少12位）", type="password", max_chars=1024)
                    confirm = st.text_input("确认初始密码", type="password", max_chars=1024)
                    if st.form_submit_button("创建审核员账号"):
                        try:
                            if password != confirm:
                                raise ValueError("两次密码不一致")
                            store.create_user(actor, username, password)
                            st.success(f"审核员账号“{username.strip()}”已创建并启用，请通过安全方式告知使用者。")
                        except ValueError as exc:
                            st.error(str(exc))
                users = store.users(actor)
                st.dataframe([{"账号": u["username"], "角色": "管理员" if u["role"] == "admin" else "审核员",
                               "状态": "已启用" if u["active"] else "已停用",
                               "登录限流": "暂时锁定" if u["blocked_until"] > time.time() else "无"}
                              for u in users], hide_index=True)
                options = [u for u in users if u["id"] != actor]
                if options:
                    selected = st.selectbox("管理账号", options, format_func=lambda u: u["username"])
                    st.caption(f"当前账号：{selected['username']}；{'已启用' if selected['active'] else '已停用'}。"
                               "账号区分大小写；登录时会忽略账号两端空格，密码中的空格会保留。")
                    if st.button("停用所选账号" if selected["active"] else "启用所选账号"):
                        store.set_active(actor, selected["id"], not selected["active"])
                        st.rerun()
                    if selected["role"] == "reviewer":
                        if st.button("解除所选账号登录限流"):
                            store.unlock_reviewer(actor, selected["id"])
                            st.success("已解除限流；停用账号仍需先启用。")
                        with st.form(f"reset_reviewer_password_{selected['id']}", clear_on_submit=True):
                            st.caption("仅点击确认按钮才会重置所选审核员密码，管理员密码不受影响。")
                            new_password = st.text_input("新密码（至少12位）", type="password", max_chars=1024)
                            confirmation = st.text_input("再次输入新密码", type="password", max_chars=1024)
                            if st.form_submit_button("确认重置所选审核员密码"):
                                try:
                                    if new_password != confirmation:
                                        raise ValueError("两次密码不一致")
                                    store.reset_reviewer_password(actor, selected["id"], new_password)
                                    st.success("审核员密码已重置并解除限流。该账号需重新登录；若已停用，仍需先启用。")
                                except ValueError as exc:
                                    st.error(str(exc))
    return store, actor


def save_task_panel(store, actor):
    if store is None or "record" not in st.session_state:
        return
    state = st.session_state
    run, _, error, _ = state["record"]
    if error or run.result is None:
        return
    with st.expander("保存任务及当前版本", expanded=not bool(state.get("task_id"))):
        st.caption("保存后可跨登录恢复。每次保存追加版本，原始文件不覆盖；未点击保存的改动只在当前会话中。")
        with st.form("save_task"):
            title = st.text_input("任务名称", value=state.get("task_title", "单证审核任务"), max_chars=100)
            save = st.form_submit_button("保存当前版本")
            save_as = st.form_submit_button("另存为新任务")
        if save or save_as:
            try:
                snapshot = pack(state["record"], state.get("review_book"), state.get("baseline"), state.get("diagnostics"))
                report = make_report(run.documents, run.result, run.language, MODES[run.mode][run.language == "en"],
                                     state.get("review_book"), run.files)
                task_id, version = store.save(actor, title, run.files, snapshot, report,
                    task_id=None if save_as else state.get("task_id"), expected_version=state.get("task_version", 0))
                state.update(task_id=task_id, task_version=version, task_title=title, task_owner=actor)
                st.success(f"已保存第 {version} 版。重新登录后从历史任务打开。")
            except (ValueError, DatabaseUnavailable) as exc:
                st.error(str(exc))
