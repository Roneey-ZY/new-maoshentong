from pathlib import Path

from core.authentication import make_config
from core.storage import Store


def test_store_cache_refreshes_when_api_version_changes(monkeypatch):
    import auth

    class PreviousStore:
        def __init__(self, *args, **kwargs):
            pass

        def initialize(self, bootstrap):
            pass

    class UpdatedStore(PreviousStore):
        pass

    auth._store.clear()
    try:
        monkeypatch.setattr(auth, "Store", PreviousStore)
        first = auth._store("sqlite:///cache-version-fixture", True, 1)
        monkeypatch.setattr(auth, "Store", UpdatedStore)
        assert auth._store("sqlite:///cache-version-fixture", True, 1) is first
        assert isinstance(auth._store("sqlite:///cache-version-fixture", True, 2), UpdatedStore)
    finally:
        auth._store.clear()


def test_admin_page_accepts_account_metadata_from_previous_release(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from task_ui import login_limit_label

    config = make_config("admin-upgrade", "test-upgrade-password")
    for key, value in {"LOGIN_USERNAME": config.username, "LOGIN_SALT": config.salt.hex(),
                       "LOGIN_PASSWORD_HASH": config.digest.hex(), "DATABASE_URL": "",
                       "TRADECHECK_LOCAL_DB": str(tmp_path / "upgrade.sqlite3")}.items():
        monkeypatch.setenv(key, value)
    original = Store.users

    def previous_metadata(self, actor):
        return [{key: value for key, value in user.items() if key not in {"failures", "blocked_until"}}
                for user in original(self, actor)]

    monkeypatch.setattr(Store, "users", previous_metadata)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    app.text_input[0].set_value(config.username)
    app.text_input[1].set_value("test-upgrade-password")
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert app.session_state["_db_login"]["role"] == "admin"
    assert any(r.label == "执行模式" for r in app.radio)
    assert any(t.label == "新账号" for t in app.text_input)
    assert "未加载" in login_limit_label({})
    assert login_limit_label({"blocked_until": 0}) == "无"
