"""Process/database restart probe for isolated local/CI fixtures only.

Never reads DATABASE_URL or production Secrets. Never stops a service itself.
"""
import argparse
import hashlib
import io
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import load_workbook

from agent.tools import ReviewRun, run_rules, revise_preview
from core.authentication import make_config
from core.demo import DATA, load_case
from core.reporter import make_report
from core.review import ReviewBook
from core.storage import Store, AccessDenied, DatabaseUnavailable
from core.task_snapshot import pack, unpack

PASSWORD = "isolated-recovery-test-only"


def fixture_store():
    dsn = os.environ.get("TEST_RECOVERY_DATABASE_URL", "")
    sqlite = os.environ.get("TEST_RECOVERY_SQLITE", "")
    if dsn:
        if urlparse(dsn).hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Recovery probe only supports an isolated loopback CI database")
        return Store(dsn, allow_local=True)
    if sqlite:
        return Store("sqlite:///" + str(Path(sqlite).resolve()), allow_local=True)
    raise ValueError("Set TEST_RECOVERY_DATABASE_URL or TEST_RECOVERY_SQLITE for isolated testing")


def prepare(path):
    if path.exists():
        raise ValueError("Probe state already exists; do not overwrite previous evidence")
    store = fixture_store()
    store.initialize(make_config("recovery-admin", PASSWORD))
    admin = store.authenticate("recovery-admin", PASSWORD)
    if not admin or store.list_tasks(admin["id"]):
        raise ValueError("Use an empty, dedicated recovery fixture database")
    actor = store.create_user(admin["id"], "recovery-reviewer", PASSWORD)
    other = store.create_user(admin["id"], "recovery-other", PASSWORD)
    original = ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
    run_rules(original)
    book = ReviewBook()
    book.record(original.result.findings[0], original.files, "explained", "partial_shipment",
                "Synthetic recovery fixture: partial shipment requires source confirmation.", "recovery-reviewer")
    report1 = make_report(original.documents, original.result, "zh", "rules", book, original.files)
    snapshot1 = pack((original, None, None, 0.1), book)
    key = str(uuid.uuid4())
    def save(_):
        return store.save(actor, "recovery-fixture", original.files, snapshot1, report1, creation_key=key)
    with ThreadPoolExecutor(max_workers=3) as pool:
        saved = list(pool.map(save, range(3)))
    assert saved == [(key, 1)] * 3
    task = key
    revised = revise_preview(original, "PL", 0, {"gross_kg": "130"})
    report2 = make_report(revised.documents, revised.result, "zh", "revision", book, revised.files)
    snapshot2 = pack((revised, None, None, 0.2), book, original.result)
    assert store.save(actor, "recovery-fixture", original.files, snapshot2, report2,
                      task_id=task, expected_version=1) == (task, 2)
    marker = {"fixture_only": True, "actor": actor, "other": other, "task": task,
        "original_hashes": {f.kind: hashlib.sha256(f.data).hexdigest() for f in original.files},
        "reports": {"1": hashlib.sha256(report1).hexdigest(), "2": hashlib.sha256(report2).hexdigest()},
        "snapshots": {"1": snapshot1, "2": snapshot2}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Prepared: 1 private fixture task, 2 immutable versions, duplicate creation suppressed")


def restore(path):
    marker = json.loads(path.read_text(encoding="utf-8"))
    store = fixture_store()
    user = store.authenticate("recovery-reviewer", PASSWORD)
    assert user and user["id"] == marker["actor"]
    assert store.authenticate("recovery-admin", PASSWORD)["role"] == "admin"
    assert len(store.versions(user["id"], marker["task"])) == 2
    for version in (1, 2):
        _, files, snapshot, report = store.load(user["id"], marker["task"], version)
        assert snapshot == marker["snapshots"][str(version)]
        assert hashlib.sha256(report).hexdigest() == marker["reports"][str(version)]
        assert {f["kind"]: hashlib.sha256(bytes(f["data"])).hexdigest() for f in files} == marker["original_hashes"]
        record, book, baseline, _ = unpack(files, snapshot, report)
        expected = {"difference": 3, "contradiction": 1 if version == 1 else 0, "review": 0}
        assert record[0].result.counts == expected and book.decisions
        workbook = load_workbook(io.BytesIO(report), read_only=True)
        assert workbook["异常与复核"].max_row == (5 if version == 1 else 4)
        assert "复核历史" in workbook.sheetnames
    assert store.list_tasks(marker["other"]) == []
    try:
        store.load(marker["other"], marker["task"], 1)
    except AccessDenied:
        pass
    else:
        raise AssertionError("Ownership isolation failed after restart")
    print("Restored: account, 2 versions, original hashes, report bytes/content, review history and ownership verified")


def unavailable(path):
    marker = json.loads(path.read_text(encoding="utf-8"))
    store = fixture_store()
    if store.local:
        raise ValueError("Database-stop probe requires PostgreSQL; SQLite is not counted")
    try:
        store.list_tasks(marker["actor"])
    except DatabaseUnavailable as exc:
        assert "postgresql://" not in str(exc) and PASSWORD not in str(exc)
        print("Unavailable database: access blocked with sanitized error, no fallback or fake success")
    else:
        raise AssertionError("Expected the isolated database to be stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["prepare", "restore", "unavailable"], required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    {"prepare": prepare, "restore": restore, "unavailable": unavailable}[args.phase](args.state)
