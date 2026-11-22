"""Download the provider's CC0 synthetic CSV files; check published byte hashes first."""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data/external/databasesample/procurement-v1"
PAGE = "https://databasesample.com/datasets/procurement-purchase-orders-and-receipts-dataset"
BASE = "https://database-sample-website.s3.eu-north-1.amazonaws.com/datasets/procurement-purchase-orders-and-receipts-dataset/v1/csv/"
EXPECTED = {
    "buyers": (5, "8420cef100a482b4f52daa70e25261355720974b937890b60a4c2deec5cb4948"),
    "items": (20, "98e30b4425a145073568a288d3cbe88d91454050f8e658c9d898d863a6391b04"),
    "requisitions": (150, "6df96335a3ee7dd06f45e8695603b822c7b302e382777cf0647abe9c4a288957"),
    "suppliers": (14, "836e406be22d37ca5f994a4abed318d1be8f2e8cb119f9c3e34d7834ff0501a7"),
    "purchase_orders": (36, "02ca1737ad6c0bb59b817417af3101e38b95692d58e09a488406bd6a4124bf31"),
    "po_lines": (91, "4001adc7f93274b06b4031555cfe5d499899babeeb89c1bf6ebf26169266780d"),
    "goods_receipts": (89, "545a2b77fc3bae949d7bc89b8fa897e77d7f56c484be0eced2720e75207a1a16"),
    "supplier_invoices": (87, "cda6d279a4ee88ae343534d9507e5403c7431d1458ffdc6bd2b9e5adacf3b461"),
}


def fetch(target=TARGET):
    if (target / "source.json").exists():
        raise ValueError("原始数据版本已存在；请校验已有版本，不要覆盖来源记录。")
    raw = target / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, (expected_rows, expected_hash) in EXPECTED.items():
        path = raw / f"{name}.csv"
        url = BASE + path.name
        if path.exists():
            content = path.read_bytes()
        else:
            with urlopen(url, timeout=30) as response:
                content = response.read(2 * 1024 * 1024)
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"来源哈希不符，拒绝接入：{name}")
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        if len(rows) != expected_rows:
            raise ValueError(f"来源行数不符：{name}")
        path.write_bytes(content)
        records[name] = {"url": url, "file": f"raw/{path.name}", "sha256": actual_hash,
                         "rows": len(rows), "columns": list(rows[0])}
        print(name, len(rows), "rows; published SHA256 verified", flush=True)
    source = {"schema": 1, "provider": "Database Sample", "title": "Procurement purchase orders and receipts dataset",
        "source_page": PAGE, "source_version": 1, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "data_nature": "third_party_public_synthetic_not_real_enterprise",
        "published_rows_total": 492, "tables": records,
        "limitations": ["Goods receipts are not packing lists.",
            "An invoice is not tied to a particular receipt in the source model.",
            "No per-document currency, recorded invoice amount, net/gross weight or accepted quantity fields.",
            "No supplier-provided per-case discrepancy labels. Local policy/reference labels are not official answers."]}
    (target / "source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    return source


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=TARGET)
    fetch(parser.parse_args().output)
