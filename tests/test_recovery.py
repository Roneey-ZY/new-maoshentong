import copy
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent.tools import ReviewRun, run_rules
from core.authentication import make_config
from core.demo import DATA, load_case
from core.storage import Store, DB, Conflict, AccessDenied, DatabaseUnavailable
from core.task_snapshot import pack

PASSWORD = "recovery-unit-test-only"
APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def context(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "private.sqlite3"), allow_local=True)
    store.initialize(make_config("admin", PASSWORD))
    actor = store.authenticate("admin", PASSWORD)["id"]
    run = ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
    run_rules(run)
    return store, actor, run, pack((run, None, None, 0.1))


def test_lost_creation_ack_and_unchanged_version_retry_are_idempotent(context, monkeypatch):
    store, actor, run, snapshot = context
    snapshot["diagnostics"] = {"columns": {2: "quantity"}}
    real = store.transaction
    drop = {"enabled": True}

    @contextmanager
    def lost_ack(*, write=False):
        with real(write=write) as db:
            yield db
        if write and drop["enabled"]:
            drop["enabled"] = False
            raise DatabaseUnavailable("Simulated response lost after commit")

    monkeypatch.setattr(store, "transaction", lost_ack)
    key = str(uuid.uuid4())
    with pytest.raises(DatabaseUnavailable):
        store.save(actor, "same", run.files, snapshot, run.report, creation_key=key)
    assert len(store.list_tasks(actor)) == 1
    assert store.save(actor, "same", run.files, snapshot, b"replacement must not be written", creation_key=key) == (key, 1)
    assert store.load(actor, key, 1)[3] == run.report
    assert store.save(actor, "same", run.files, snapshot, run.report, task_id=key, expected_version=1) == (key, 1)
    changed = copy.deepcopy(snapshot)
    changed["elapsed"] = 0.2
    assert store.save(actor, "same", run.files, changed, run.report, task_id=key, expected_version=1) == (key, 2)
    with pytest.raises(Conflict):
        store.save(actor, "same", run.files, snapshot, run.report, task_id=key, expected_version=1)
    intruder = store.create_user(actor, "intruder", PASSWORD)
    with pytest.raises(AccessDenied):
        store.save(intruder, "same", run.files, changed, run.report, creation_key=key)


def test_concurrent_first_saves_create_one_task_and_one_version(context):
    store, actor, run, snapshot = context
    key = str(uuid.uuid4())
    def save(_):
        return store.save(actor, "same", run.files, snapshot, run.report, creation_key=key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(save, range(4))) == [(key, 1)] * 4
    assert len(store.list_tasks(actor)) == len(store.versions(actor, key)) == 1


def test_failed_write_rolls_back_task_files_and_version(context, monkeypatch):
    store, actor, run, snapshot = context
    actual = DB.run
    fault = {"enabled": True}
    def failing(db, sql, values=()):
        if fault["enabled"] and sql.startswith("UPDATE tc_tasks SET version"):
            raise OSError("driver detail must not be exposed")
        return actual(db, sql, values)
    monkeypatch.setattr(DB, "run", failing)
    key = str(uuid.uuid4())
    with pytest.raises(DatabaseUnavailable) as error:
        store.save(actor, "same", run.files, snapshot, run.report, creation_key=key)
    assert "driver detail" not in str(error.value)
    assert not store.list_tasks(actor)
    fault["enabled"] = False
    assert store.save(actor, "same", run.files, snapshot, run.report, creation_key=key) == (key, 1)


def test_broken_rollback_does_not_expose_driver_error(context, monkeypatch):
    store, _, _, _ = context
    import core.storage as module
    class Broken:
        def execute(self, *args):
            raise OSError("SECRET_DRIVER_DETAIL_FIXTURE")
        def rollback(self):
            raise OSError("SECRET_ROLLBACK_DETAIL_FIXTURE")
        def close(self):
            raise OSError("SECRET_CLOSE_DETAIL_FIXTURE")
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args, **kwargs: Broken())
    with pytest.raises(DatabaseUnavailable) as error:
        with store.transaction(write=True):
            raise AssertionError("Must not yield")
    assert "SECRET" not in str(error.value)


def configure(monkeypatch, path):
    config = make_config("admin", PASSWORD)
    for key, value in {"LOGIN_USERNAME": config.username, "LOGIN_SALT": config.salt.hex(),
                       "LOGIN_PASSWORD_HASH": config.digest.hex(), "DATABASE_URL": "",
                       "TRADECHECK_LOCAL_DB": str(path)}.items():
        monkeypatch.setenv(key, value)


def button(app, label):
    return next(b for b in app.button if b.label == label)


def login(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    configure(monkeypatch, tmp_path / "ui.sqlite3")
    app = AppTest.from_file(str(APP)).run(timeout=30)
    app.text_input[0].set_value("admin")
    app.text_input[1].set_value(PASSWORD)
    button(app, "登录").click().run(timeout=30)
    assert not app.exception and "_db_login" in app.session_state
    return app


def test_database_outage_fails_closed_and_retry_preserves_unsaved_session(monkeypatch, tmp_path):
    app = login(monkeypatch, tmp_path)
    next(r for r in app.radio if r.label == "执行模式").set_value("rules").run()
    button(app, "一键加载演示案例").click().run(timeout=30)
    before = app.session_state["record"][0].result
    original = Store.user
    fault = {"enabled": True}
    def unavailable(store, uid):
        if fault["enabled"]:
            raise DatabaseUnavailable("fixture")
        return original(store, uid)
    monkeypatch.setattr(Store, "user", unavailable)
    app.run(timeout=30)
    assert not app.exception and any("账号服务暂时不可用" in e.value for e in app.error)
    assert not app.get("file_uploader") and not app.radio
    assert app.session_state["record"][0].result == before
    fault["enabled"] = False
    button(app, "重试连接").click().run(timeout=30)
    assert not app.exception and app.session_state["record"][0].result == before
    assert any(r.label == "执行模式" for r in app.radio)


def test_ui_retries_a_committed_save_without_duplicate_task(monkeypatch, tmp_path):
    app = login(monkeypatch, tmp_path)
    next(r for r in app.radio if r.label == "执行模式").set_value("rules").run()
    button(app, "一键加载演示案例").click().run(timeout=30)
    actual = Store.save
    fault = {"enabled": True}
    def lost_reply(store, *args, **kwargs):
        result = actual(store, *args, **kwargs)
        if fault["enabled"]:
            fault["enabled"] = False
            raise DatabaseUnavailable("fixture lost reply")
        return result
    monkeypatch.setattr(Store, "save", lost_reply)
    button(app, "保存当前版本").click().run(timeout=30)
    assert not app.exception and any("未能确认保存结果" in e.value for e in app.error)
    pending = app.session_state["_pending_creation"]["id"]
    button(app, "保存当前版本").click().run(timeout=30)
    assert not app.exception and app.session_state["task_id"] == pending and app.session_state["task_version"] == 1
    from auth import get_store
    # A separate store connection verifies persisted state, independent of UI cache.
    store = Store("sqlite:///" + str(tmp_path / "ui.sqlite3"), allow_local=True)
    assert len(store.list_tasks(app.session_state["_db_login"]["id"])) == 1


def test_model_http_503_does_not_fabricate_success_and_rule_fallback_works(monkeypatch, tmp_path):
    import httpx2 as httpx
    from openai import AsyncOpenAI
    import agent.main_agent as module
    def failed_client(*args):
        async def handler(request):
            return httpx.Response(503, json={"error": {"message": "PRIVATE_PROVIDER_DETAIL_FIXTURE", "type": "server_error"}})
        return AsyncOpenAI(api_key="not-real-model-key", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, "model_client", failed_client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-real-model-key")
    app = login(monkeypatch, tmp_path)
    next(r for r in app.radio if r.label == "执行模式").set_value("ai").run()
    button(app, "一键加载演示案例").click().run(timeout=30)
    assert not app.exception
    run, summary, error, _ = app.session_state["record"]
    assert error and "503" in error and "PRIVATE_PROVIDER" not in error
    assert run.report is None and run.log == [] and summary is None
    original_files = run.files
    button(app, "对同一组单证运行规则演示").click().run(timeout=30)
    assert not app.exception
    run, summary, error, _ = app.session_state["record"]
    assert run.mode == "rules" and run.files == original_files and error is None
    assert run.result.counts == {"difference": 3, "contradiction": 1, "review": 0}


def test_saved_versions_survive_a_fresh_python_process(tmp_path):
    # SQLite process restart is local adapter evidence, not PostgreSQL evidence.
    env = dict(os.environ, TEST_RECOVERY_SQLITE=str(tmp_path / "restart.sqlite3"))
    env.pop("TEST_RECOVERY_DATABASE_URL", None)
    marker = tmp_path / "state.json"
    startup = "import os,site,runpy,sys;site.addsitedir(os.environ.get('PYTHONPATH',''));" \
              "sys.argv=['recovery_probe',*sys.argv[1:]];runpy.run_module('scripts.recovery_probe',run_name='__main__')"
    for phase in ("prepare", "restore"):
        result = subprocess.run([sys.executable, "-c", startup, "--phase", phase, "--state", str(marker)],
            cwd=APP.parent, env=env, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
