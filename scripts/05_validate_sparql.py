"""05 - Run SPARQL competency queries against the Virtuoso endpoint.

Inputs:
    outputs/_state.json               (graph URI from 04_load_virtuoso.py)
    sparql-queries/*.rq               (competency queries)
Outputs:
    outputs/_validation.json          (pass/fail per query + result rows)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.virtuoso_client import VirtuosoConfig, SparqlClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", default=REPO_ROOT / "outputs" / "_state.json", type=Path)
    parser.add_argument("--sparql-dir", default=REPO_ROOT / "sparql-queries", type=Path)
    parser.add_argument("--out", default=REPO_ROOT / "outputs" / "_validation.json", type=Path)
    args = parser.parse_args()

    config = VirtuosoConfig.from_env()
    client = SparqlClient(config)

    # Verify connectivity
    try:
        client.execute_select("SELECT 1 AS ?ping WHERE {}")
    except Exception as exc:
        print(f"Virtuoso unreachable at {config.sparql_url}: {exc}")
        sys.exit(1)

    if args.state.exists():
        state = json.loads(args.state.read_text(encoding="utf-8"))
        print(f"Graph: {state.get('graphUri', '?')}  ({state.get('tripleCount', '?')} triples)")

    rq_files = sorted(args.sparql_dir.glob("*.rq"))
    if not rq_files:
        print(f"No .rq files in {args.sparql_dir}")
        sys.exit(0)

    print(f"\nExecuting {len(rq_files)} SPARQL queries...\n")
    per_query: list[dict] = []
    passed = failed = 0

    for rq_file in rq_files:
        content = rq_file.read_text(encoding="utf-8")
        header_lines, body_lines = [], []
        for line in content.strip().split("\n"):
            if line.strip().startswith("#"):
                header_lines.append(line.strip().lstrip("# ").strip())
            else:
                body_lines.append(line)
        query = "\n".join(body_lines).strip()

        print(f"--- {rq_file.name} ---")
        for h in header_lines:
            print(f"  {h}")
        print()

        record: dict = {"file": rq_file.name, "headers": header_lines, "query": query}
        try:
            result = client.execute_query(query)
            bindings = result.get("results", {}).get("bindings", [])
            columns = result.get("head", {}).get("vars", [])

            if columns:
                header = " | ".join(columns)
                print(f"  {header}")
                print(f"  {'-' * len(header)}")
            for row in bindings[:10]:
                vals = [row.get(c, {}).get("value", "") for c in columns]
                print(f"  {' | '.join(vals)}")
            print(f"  ({len(bindings)} rows)")
            record.update({"status": "passed", "row_count": len(bindings), "columns": columns})
            passed += 1
        except Exception as exc:
            print(f"  ERROR: {exc}")
            record.update({"status": "failed", "error": str(exc)})
            failed += 1
        per_query.append(record)
        print()

    out = {
        "passed": passed,
        "failed": failed,
        "total": len(rq_files),
        "queries": per_query,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(f"Results: {passed} passed, {failed} failed out of {len(rq_files)}  -> {args.out}")


if __name__ == "__main__":
    main()
