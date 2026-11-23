"""Gate the entire workbench before any documents or model actions are exposed."""
import os
from time import monotonic

import streamlit as st

from core.authentication import LoginConfig, LoginLimiter, SESSION_SECONDS, valid_session
from core.storage import Store, DatabaseUnavailable, STORAGE_API_VERSION
from design_system import login_heading


def setting(name):
    # Environment variables support Tencent CloudBase; Secrets supports Community Cloud.
    if name in os.environ:
        return os.environ[name]
    try:
        return str(st.secrets.get(name, ""))
    except FileNotFoundError:
        return ""


@st.cache_resource(show_spinner=False)
def login_limiter(revision):
    return LoginLimiter()


@st.cache_resource(show_spinner=False)
def _store(dsn, local, api_version):
    # Streamlit caches instances across reruns. Imported class changes alone do
    # not invalidate this factory; an explicit version prevents stale methods.
    store = Store(dsn, allow_local=local)
    try:
        bootstrap = LoginConfig.load(setting)
    except ValueError:
        bootstrap = None
    store.initialize(bootstrap)
    return store


def get_store():
    dsn = setting("DATABASE_URL")
    local_path = os.environ.get("TRADECHECK_LOCAL_DB", "")
    if not dsn and local_path:
        return _store("sqlite:///" + local_path, True, STORAGE_API_VERSION)
    return _store(dsn, False, STORAGE_API_VERSION) if dsn else None


def current_username():
    return st.session_state.get("_db_login", {}).get("username") or setting("LOGIN_USERNAME")


def database_login(store):
    session = st.session_state.get("_db_login", {})
    user = store.user(session.get("id", "")) if session else None
    if user and session.get("revision") == user["revision"] and monotonic() < session.get("until", 0):
        with st.sidebar:
            st.caption(f"当前账号：{user['username']} · {'管理员' if user['role'] == 'admin' else '审核员'}")
            if st.button("退出登录", key="logout"):
                st.session_state.clear()
                st.rerun()
        return user
    if session or st.session_state.get("_login"):
        st.session_state.clear()
    login_heading()
    with st.container(key="tc_login_panel"), st.form("database_login", clear_on_submit=True):
        username = st.text_input("账号", max_chars=128)
        password = st.text_input("密码", type="password", max_chars=1024)
        submitted = st.form_submit_button("登录", type="primary")
    if submitted:
        user = store.authenticate(username, password)
        if user:
            st.session_state.clear()
            st.session_state["_db_login"] = {**user, "until": monotonic() + SESSION_SECONDS}
            st.rerun()
        st.error("账号或密码错误、账号已停用，或尝试过于频繁。请稍后重试。")
    st.caption("退出会清理浏览器会话，已经主动保存的历史任务仍可在重新登录后打开。")
    st.stop()


def require_login():
    try:
        store = get_store()
        if store is not None:
            return database_login(store)
    except (DatabaseUnavailable, ValueError):
        st.error("任务存储或账号服务暂时不可用，请联系管理员检查数据库配置。")
        st.caption("未保存的改动尚未落库，请保留当前页面，连接恢复后点击重试。若持续失败，请联系管理员检查服务配置。")
        if st.button("重试连接"):
            st.rerun()
        st.stop()
    try:
        config = LoginConfig.load(setting)
    except ValueError:
        st.title("贸审通 TradeCheck Agent")
        st.error("登录服务尚未配置完成，请联系管理员。")
        st.stop()

    session = st.session_state.get("_login", {})
    if valid_session(session, config):
        with st.sidebar:
            st.caption(f"当前账号：{config.username}")
            if st.button("退出登录", key="logout"):
                st.session_state.clear()
                st.rerun()
        return
    if session:
        st.session_state.clear()

    login_heading()
    with st.container(key="tc_login_panel"), st.form("login_form", clear_on_submit=True):
        username = st.text_input("账号", max_chars=128)
        password = st.text_input("密码", type="password", max_chars=1024)
        submitted = st.form_submit_button("登录", type="primary")
    if submitted:
        accepted, wait = login_limiter(config.revision).attempt(config, username, password)
        if accepted:
            st.session_state.clear()
            st.session_state["_login"] = {
                "revision": config.revision, "until": monotonic() + SESSION_SECONDS,
            }
            st.rerun()
        elif wait:
            st.error(f"登录尝试过于频繁，请 {wait} 秒后重试。")
        else:
            st.error("账号或密码错误。连续失败 5 次后将暂停登录 60 秒。")
    st.caption("登录有效期为 8 小时。退出登录会清除当前会话中的单证与审核结果。")
    st.stop()
