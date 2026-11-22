"""Replay one previously observed unsafe suggestion. This is regression, not holdout."""
import hashlib
import json
from pathlib import Path

from agent.diagnostic_agent import apply_proposal, revision_key, Context, inspect_candidate_tables
from agent.tools import ReviewRun, run_rules
from core.demo import DATA, load_case
from core.parser import ParseError


def replay():
    source = DATA / "independent_results/v1-live/comparison.json"
    experiment = json.loads(source.read_text(encoding="utf-8"))
    previous = next(row for row in experiment["arms"]["agent_test_confirmation"]["records"]
                    if row["case"] == "21-manual-boundaries-duplicate")
    run = ReviewRun(load_case(DATA / "independent_validation/v1" / previous["case"]), "zh", "rules")
    run_rules(run)
    before = sum(len(doc.items) for doc in run.documents)
    before_result = run.result.model_dump(mode="json")
    context = Context(run)
    inspect_candidate_tables(context)
    blocked = False
    try:
        revised = apply_proposal(run, {"revision": revision_key(run),
            "proposals": [previous["confirmations"][0]["proposal"]]}, 0)
    except ParseError:
        blocked = True
        revised = run
    after = sum(len(doc.items) for doc in revised.documents)
    result = {"type": "observed_failure_regression_not_holdout", "case": previous["case"],
        "new_model_requests": 0, "previous_result_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "previous_bad_proposal": previous["confirmations"][0]["proposal"],
        "blocked_unsafe_mapping": blocked, "item_rows_before": before, "item_rows_after": after,
        "result_unchanged": revised.result.model_dump(mode="json") == before_result,
        "duplicate_review_preserved": any(f.rule == "SKU_DUPLICATE" for f in revised.result.findings)}
    destination = DATA / "independent_results/postfix-safety-regression.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(result[key] for key in ["blocked_unsafe_mapping", "result_unchanged", "duplicate_review_preserved"]):
        raise AssertionError("Observed unsafe mapping regression did not pass")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    replay()
