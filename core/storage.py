"""Transactional task storage. SQLite is explicit local development only.

Files and version snapshots are private database values, never public download URLs.
All task access checks owner ID server-side; admins may read, not overwrite others.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from core.authentication import LoginConfig, make_config


class AccessDenied(ValueError):
    pass


class Conflict(ValueError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


class Store:
    def __init__(self, dsn, *, allow_local=False):
        self.dsn = dsn
        self.local = dsn.startswith("sqlite:///")
        if self.local:
            if not allow_local:
                raise ValueError("SQLite 只用于显式启用的本地开发，云端请配置 PostgreSQL。")
            self.path = dsn[len("sqlite:///"):]
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        else:
            parsed = urlparse(dsn)
            if parsed.scheme not in {"postgres", "postgresql"}:
                raise ValueError("数据库地址格式不支持")
            self.allow_local = allow_local and parsed.hostname in {"localhost", "127.0.0.1"}

    @contextmanager
    def transaction(self, *, write=False):
        conn = None
        try:
            if self.local:
                conn = sqlite3.connect(self.path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                if write:
                    conn.execute("BEGIN IMMEDIATE")
            else:
                import psycopg
                from psycopg.rows import dict_row
                from psycopg.conninfo import conninfo_to_dict
                params = conninfo_to_dict(self.dsn)
                if not self.allow_local:
                    if params.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}:
                        raise ValueError("云端数据库连接必须启用 TLS")
                    params.setdefault("sslmode", "require")
                conn = psycopg.connect(**params, connect_timeout=8, row_factory=dict_row)
            yield DB(conn, self.local)
            conn.commit()
        except (AccessDenied, Conflict, ValueError):
            if conn:
                conn.rollback()
            raise
        except Exception:
            if conn:
                conn.rollback()
            raise DatabaseUnavailable("存储操作未完成，请检查服务端数据库配置或稍后重试。") from None
        finally:
            if conn:
                conn.close()

    def initialize(self, bootstrap=None):
        with self.transaction(write=True) as db:
            for statement in SCHEMA:
                db.run(statement.replace("FILE_BYTES", "BLOB" if self.local else "BYTEA"))
            if bootstrap and db.one("SELECT COUNT(*) AS n FROM tc_users")["n"] == 0:
                db.run("INSERT INTO tc_users VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(username) DO NOTHING",
                       (str(uuid.uuid4()), bootstrap.username, "admin", bootstrap.salt.hex(),
                        bootstrap.digest.hex(), 1, 0, 0.0))

    def user(self, uid):
        with self.transaction() as db:
            row = db.one("SELECT * FROM tc_users WHERE id=? AND active=1", (uid,))
            return public_user(row) if row else None

    def authenticate(self, username, password):
        with self.transaction(write=True) as db:
            query = "SELECT * FROM tc_users WHERE username=?"
            if not self.local:
                query += " FOR UPDATE"
            row = db.one(query, (username,))
            if not row or not row["active"]:
                # Hash a dummy input as well: unknown accounts do not skip expensive work.
                LoginConfig("dummy", b"0" * 16, b"0" * 32).verify(username, password)
                return None
            if row["blocked_until"] > time.time():
                return None
            config = LoginConfig(row["username"], bytes.fromhex(row["salt"]), bytes.fromhex(row["digest"]))
            if config.verify(username, password):
                db.run("UPDATE tc_users SET failures=0, blocked_until=0 WHERE id=?", (row["id"],))
                return public_user(row)
            failures = row["failures"] + 1 if row["blocked_until"] == 0 else 1
            db.run("UPDATE tc_users SET failures=?, blocked_until=? WHERE id=?",
                   (failures, time.time() + 60 if failures >= 5 else 0, row["id"]))
            return None

    def create_user(self, actor, username, password):
        config = make_config(username, password)
        with self.transaction(write=True) as db:
            self._actor(db, actor, admin=True)
            if db.one("SELECT id FROM tc_users WHERE username=?", (config.username,)):
                raise ValueError("该账号已存在")
            uid = str(uuid.uuid4())
            db.run("INSERT INTO tc_users VALUES (?,?,?,?,?,?,?,?)",
                   (uid, config.username, "reviewer", config.salt.hex(), config.digest.hex(), 1, 0, 0.0))
            return uid

    def users(self, actor):
        with self.transaction() as db:
            self._actor(db, actor, admin=True)
            return [dict(r) for r in db.all("SELECT id,username,role,active FROM tc_users ORDER BY username")]

    def set_active(self, actor, uid, active):
        with self.transaction(write=True) as db:
            self._actor(db, actor, admin=True)
            if uid == actor:
                raise ValueError("不能停用当前管理员")
            db.run("UPDATE tc_users SET active=? WHERE id=?", (int(active), uid))

    def _actor(self, db, uid, admin=False):
        row = db.one("SELECT * FROM tc_users WHERE id=? AND active=1", (uid,))
        if not row or (admin and row["role"] != "admin"):
            raise AccessDenied("没有访问权限")
        return row

    def _task(self, db, actor, task_id, *, write=False):
        user = self._actor(db, actor)
        query = "SELECT * FROM tc_tasks WHERE id=?"
        if write and not self.local:
            query += " FOR UPDATE"
        task = db.one(query, (task_id,))
        if not task or (task["owner_id"] != actor and (write or user["role"] != "admin")):
            raise AccessDenied("任务不存在或没有访问权限")
        return task

    def list_tasks(self, actor):
        with self.transaction() as db:
            user = self._actor(db, actor)
            columns = "id,owner_id,title,created_at,version"
            return [dict(r) for r in db.all(
                f"SELECT {columns} FROM tc_tasks" + ("" if user["role"] == "admin" else " WHERE owner_id=?")
                + " ORDER BY created_at DESC LIMIT 100", () if user["role"] == "admin" else (actor,))]

    def save(self, actor, title, files, snapshot, report, *, task_id=None, expected_version=0):
        title = title.strip()
        if not title or len(title) > 100:
            raise ValueError("任务名称须为 1 至 100 个字符")
        payload = json.dumps(snapshot, ensure_ascii=False)
        if len(payload.encode()) > 10 * 1024 * 1024:
            raise ValueError("任务快照超过保存上限")
        if len(files) != 3 or {f.kind for f in files} != {"PO", "CI", "PL"}:
            raise ValueError("任务须包含三类单证各一份")
        if any(len(f.data) > 8 * 1024 * 1024 for f in files):
            raise ValueError("原文件超过保存上限")
        if len(report) > 10 * 1024 * 1024:
            raise ValueError("报告超过保存上限")
        with self.transaction(write=True) as db:
            self._actor(db, actor)
            if task_id:
                task = self._task(db, actor, task_id, write=True)
                if task["version"] != expected_version:
                    raise Conflict("任务已有新版本，请重新打开最新版本，或另存为新任务。")
                stored = db.all("SELECT kind,sha256 FROM tc_files WHERE task_id=?", (task_id,))
                actual = {f.kind: hashlib.sha256(f.data).hexdigest() for f in files}
                if {r["kind"]: r["sha256"] for r in stored} != actual:
                    raise ValueError("不能替换已保存任务的原始文件，请另存新任务。")
                version = task["version"] + 1
            else:
                task_id = str(uuid.uuid4())
                version = 1
                db.run("INSERT INTO tc_tasks VALUES (?,?,?,?,?)", (task_id, actor, title, time.time(), 0))
                for f in files:
                    db.run("INSERT INTO tc_files VALUES (?,?,?,?,?)", (task_id, f.kind, f.name,
                           hashlib.sha256(f.data).hexdigest(), f.data))
            db.run("INSERT INTO tc_versions VALUES (?,?,?,?,?,?)",
                   (task_id, version, time.time(), actor, payload, report))
            db.run("UPDATE tc_tasks SET version=?,title=? WHERE id=?", (version, title, task_id))
            return task_id, version

    def versions(self, actor, task_id):
        with self.transaction() as db:
            self._task(db, actor, task_id)
            return [dict(r) for r in db.all("SELECT version,created_at,actor_id FROM tc_versions WHERE task_id=? ORDER BY version DESC", (task_id,))]

    def load(self, actor, task_id, version):
        with self.transaction() as db:
            task = dict(self._task(db, actor, task_id))
            row = db.one("SELECT * FROM tc_versions WHERE task_id=? AND version=?", (task_id, version))
            if not row:
                raise ValueError("审核版本不存在")
            files = [dict(r) for r in db.all("SELECT kind,name,sha256,data FROM tc_files WHERE task_id=?", (task_id,))]
            return task, files, json.loads(row["snapshot"]), bytes(row["report"])


def public_user(row):
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "revision": hashlib.sha256((row["salt"] + row["digest"] + row["role"]).encode()).hexdigest()}


class DB:
    def __init__(self, conn, local):
        self.conn, self.local = conn, local

    def run(self, sql, values=()):
        return self.conn.execute(sql if self.local else sql.replace("?", "%s"), values)

    def one(self, sql, values=()):
        return self.run(sql, values).fetchone()

    def all(self, sql, values=()):
        return self.run(sql, values).fetchall()


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS tc_users (
        id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
        salt TEXT NOT NULL, digest TEXT NOT NULL, active INTEGER NOT NULL,
        failures INTEGER NOT NULL, blocked_until DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tc_tasks (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES tc_users(id), title TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL, version INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tc_files (
        task_id TEXT NOT NULL REFERENCES tc_tasks(id), kind TEXT NOT NULL, name TEXT NOT NULL,
        sha256 TEXT NOT NULL, data FILE_BYTES NOT NULL, PRIMARY KEY(task_id,kind))""",
    """CREATE TABLE IF NOT EXISTS tc_versions (
        task_id TEXT NOT NULL REFERENCES tc_tasks(id), version INTEGER NOT NULL,
        created_at DOUBLE PRECISION NOT NULL, actor_id TEXT NOT NULL REFERENCES tc_users(id),
        snapshot TEXT NOT NULL, report FILE_BYTES NOT NULL, PRIMARY KEY(task_id,version))""",
]
