import json
from pathlib import Path

import pytest

from core.parser import checked_rows
from scripts.evaluate_independent import run_case
from scripts.summarize_human_study import summarize

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_metadata_does_not_hide_supported_csv_item_headers(delimiter):
    text = f'Order reference{delimiter}PO-1\n\nsku{delimiter}quantity{delimiter}unit_price\nA-1{delimiter}5{delimiter}20\n'
    rows = list(checked_rows(text.encode(), ".csv"))
    assert rows[0][1] == 1 and rows[2][1] == 3
    assert rows[2][2] == ["sku", "quantity", "unit_price"]
    assert rows[3][2] == ["A-1", "5", "20"]


def test_observed_semicolon_failures_are_regression_not_new_holdout():
    # These four cases failed in d539f3d; repairing delimiter inference must not
    # rewrite their first observed model/oracle results.
    folder = ROOT / "data/optimization_validation/v2"
    original = (ROOT / "data/optimization_results/holdout-v2/comparison.json").read_bytes()
    for case in sorted(folder.glob("*new-reference*")):
        row, report = run_case(case, "oracle_test_confirmation")
        assert row["success"] and report
        assert row["confirmation_method"] == "program_simulation_not_actual_human"
    assert original == (ROOT / "data/optimization_results/holdout-v2/comparison.json").read_bytes()


def test_empty_human_records_never_become_measured_success(tmp_path):
    template = ROOT / "validation/human-study-template.csv"
    with pytest.raises(ValueError, match="没有真人"):
        summarize(template)
    recorded = tmp_path / "fixture.csv"
    recorded.write_text(template.read_text(encoding="utf-8") +
        "TEST-FIXTURE,1,2026-11-23T10:00:00+08:00,2026-11-23T10:02:00+08:00,true,0,0,4,unit test fixture only\n", encoding="utf-8")
    result = summarize(recorded)
    assert result["participants"] == 1 and result["overall"]["median_observed_seconds"] == 120
    recorded.write_text(recorded.read_text(encoding="utf-8").replace("true,0", "true,2"), encoding="utf-8")
    with pytest.raises(ValueError, match="矛盾"):
        summarize(recorded)


def test_new_evaluation_ui_is_read_only_and_retains_failures():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string("from optimization_ui import optimization_panel\noptimization_panel()").run(timeout=30)
    assert not app.exception and len(app.get("download_button")) == 2
    assert any("没有真人" in message.value for message in app.info)
    assert len(app.dataframe) == 2
