"""08 - Compare agent query results to gold SPARQL result sets.

Inputs:
    outputs/_agent_comparison.json
    gold-queries/*.rq
Outputs:
    outputs/_result_validation.json
    (prints comparison table)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.virtuoso_client import SparqlClient  # noqa: E402


def _result_to_tuple_set(result_json: str) -> set | None:
    """Parse a SPARQL JSON result string into a frozenset of sorted row tuples."""
    if not result_json or not result_json.strip():
        return None
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        cols = sorted(data[0].keys())
        return frozenset(tuple(row.get(c, "") for c in cols) for row in data)
    return None


def _gold_query_path(scenario_id: str) -> Path:
    return REPO_ROOT / "gold-queries" / f"{scenario_id}.rq"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--comparison",
        default=REPO_ROOT / "outputs" / "_agent_comparison.json",
        type=Path,
    )
    parser.add_argument(
        "--out",
        default=REPO_ROOT / "outputs" / "_result_validation.json",
        type=Path,
    )
    args = parser.parse_args()

    client = SparqlClient()
    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))

    results: list[dict] = []

    for row in comparison["perQuestion"]:
        sid = row["scenario_id"]
        gold_path = _gold_query_path(sid)

        entry = {"scenario_id": sid}

        if not gold_path.exists():
            entry["gold_exists"] = False
            results.append(entry)
            continue
        entry["gold_exists"] = True

        # Run gold query
        gold_query = gold_path.read_text(encoding="utf-8")
        gold_rows = client.execute_select(gold_query)
        gold_set = _result_to_tuple_set(json.dumps(gold_rows))

        # Compare ontology agent results
        onto_results = row.get("ontology_results", [])
        if onto_results and gold_set is not None:
            onto_set = _result_to_tuple_set(onto_results[-1])
            onto_match = (onto_set == gold_set) if onto_set is not None else False
        else:
            onto_match = False
        entry["ontology_result_match"] = onto_match

        # Compare naked agent results
        naked_results = row.get("naked_results", [])
        if naked_results and gold_set is not None:
            naked_set = _result_to_tuple_set(naked_results[-1])
            naked_match = (naked_set == gold_set) if naked_set is not None else False
        else:
            naked_match = False
        entry["naked_result_match"] = naked_match

        # Details for debugging
        entry["gold_row_count"] = len(gold_set) if gold_set else 0
        entry["ontology_result_row_count"] = len(onto_set) if onto_match or onto_set else None  # noqa: F821
        entry["naked_result_row_count"] = len(naked_set) if naked_match or naked_set else None  # noqa: F821

        results.append(entry)

    # Summary
    onto_matches = sum(1 for r in results if r.get("ontology_result_match"))
    naked_matches = sum(1 for r in results if r.get("naked_result_match"))
    total = sum(1 for r in results if r.get("gold_exists"))

    out = {
        "total_with_gold": total,
        "ontology_matches": onto_matches,
        "naked_matches": naked_matches,
        "per_scenario": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"Query-level result validation ({total} scenarios with gold queries):")
    print(f"  OntologyAgent: {onto_matches}/{total} result sets match gold")
    print(f"  NakedAgent:    {naked_matches}/{total} result sets match gold")
    print()

    # Per-scenario table
    print(f"{'ID':4s} {'Gold rows':>10s} {'Ontology':>10s} {'Naked':>10s}")
    print(f"{'---':4s} {'---------':>10s} {'--------':>10s} {'-----':>10s}")
    for r in results:
        sid = r["scenario_id"]
        gold_rows = r.get("gold_row_count", "?")
        o = "MATCH" if r.get("ontology_result_match") else "miss"
        n = "MATCH" if r.get("naked_result_match") else "miss"
        print(f"{sid:4s} {str(gold_rows):>10s} {o:>10s} {n:>10s}")

    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
