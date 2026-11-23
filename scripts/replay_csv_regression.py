"""Re-run four observed delimiter failures, under predefined mapping conditions."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.evaluate_independent import run_case, aggregate, hash_file
from scripts.evaluate_optimization import engine_hashes

ROOT = Path(__file__).resolve().parents[1]


def replay(output):
    if output.exists():
        raise ValueError("不能覆盖已记录的回归试验。")
    rows = []
    dataset = ROOT / "data/optimization_validation/v2"
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if hash_file(dataset / name) != expected:
            raise ValueError("冻结样本变化，停止回放。")
    for folder in sorted(dataset.glob("*new-reference*")):
        row, _ = run_case(folder, "oracle_test_confirmation")
        rows.append(row)
    result = {"experiment_type": "known_csv_failure_regression_not_new_holdout",
              "condition": "predefined_correct_mapping_simulates_human_confirmation_no_model",
              "created_utc": datetime.now(timezone.utc).isoformat(), "engine": engine_hashes(),
              "source_measurement": "data/optimization_results/holdout-v2/comparison.json",
              "summary": aggregate(rows), "records": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    result = replay(parser.parse_args().output)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
