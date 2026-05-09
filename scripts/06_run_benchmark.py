"""06 - Run the 18 benchmark scenarios through both LangChain agents.

Inputs:
    scenarios/truck_scenarios.json
    input/data/seed/*.jsonl          (for SQLite setup)
    outputs/_state.json              (graph URI)
Outputs:
    outputs/_agent_comparison.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.agents.runner import run_benchmark  # noqa: E402
from truck_bench.markdown_parser import parse_markdown  # noqa: E402
from truck_bench.sqlite_client import load_jsonl_to_sqlite  # noqa: E402
from truck_bench.virtuoso_client import SparqlClient, VirtuosoConfig  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scenarios",
        default=REPO_ROOT / "scenarios" / "truck_scenarios.json",
        type=Path,
    )
    parser.add_argument(
        "--seed-dir",
        default=REPO_ROOT / "input" / "data" / "seed",
        type=Path,
    )
    parser.add_argument(
        "--md",
        default=REPO_ROOT / "input" / "schema" / "ontology.md",
        type=Path,
    )
    parser.add_argument(
        "--db",
        default=REPO_ROOT / "outputs" / "truck_bench.db",
        type=Path,
    )
    parser.add_argument(
        "--state",
        default=REPO_ROOT / "outputs" / "_state.json",
        type=Path,
    )
    parser.add_argument(
        "--out",
        default=REPO_ROOT / "outputs" / "_agent_comparison.json",
        type=Path,
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Max LangChain agent tool-calling iterations per question.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds per question.",
    )
    args = parser.parse_args()

    # Load scenarios
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    print(f"Scenarios: {len(scenarios)} questions from {args.scenarios}")

    # SQLite setup
    parsed = parse_markdown(args.md)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSetting up SQLite at {args.db} ...")
    counts = load_jsonl_to_sqlite(str(args.db), args.seed_dir, parsed)
    total_rows = sum(counts.values())
    print(f"  {total_rows} rows across {len(counts)} tables")

    # Virtuoso SPARQL client
    config = VirtuosoConfig.from_env()
    client = SparqlClient(config)
    try:
        client.execute_select("SELECT 1 AS ?ping WHERE {}")
        print(f"\nVirtuoso connected at {config.sparql_url}")
    except Exception as exc:
        print(f"\nVirtuoso unreachable at {config.sparql_url}: {exc}")
        print("  The OntologyAgent will fail — ensure Virtuoso is running and data is loaded.")
        print("  Run: python scripts/04_load_virtuoso.py")

    def _sparql_query_fn(query: str) -> list[dict]:
        return client.execute_select(query)

    print(f"\nRunning benchmark (max_iterations={args.max_iterations}, timeout={args.timeout}s)...")
    result = run_benchmark(
        scenarios=scenarios,
        db_path=str(args.db),
        sparql_query_fn=_sparql_query_fn,
        max_iterations=args.max_iterations,
        timeout_seconds=args.timeout,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # Summary
    a = result["agents"]
    print(f"\n{'=' * 60}")
    print(f"  Benchmark complete → {args.out}")
    print(f"  NakedAgent:    {a['naked']['correctCount']}/{a['naked']['scoredQuestions']} "
          f"({a['naked']['accuracyPct']}%)")
    print(f"  OntologyAgent: {a['ontology']['correctCount']}/{a['ontology']['scoredQuestions']} "
          f"({a['ontology']['accuracyPct']}%)")
    print(f"{'=' * 60}")

    print("\nNext: python scripts/06_score.py")


if __name__ == "__main__":
    main()
