"""05 - Run SPARQL or GQL competency queries against the Virtuoso endpoint.

Inputs:
    outputs/_state.json               (graph URI from 04_load_virtuoso.py)
    sparql-queries/*.rq  or  virtuoso-gql-queries/*.gql
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


def _parse_query_file(path: Path) -> tuple[list[str], str]:
    """Extract comments and query body from a .rq or .gql file.

    .rq files use # for comments.  .gql files use // for comments.
    """
    content = path.read_text(encoding="utf-8")
    is_gql = path.suffix == ".gql"
    comment_prefix = "//" if is_gql else "#"

    header_lines: list[str] = []
    body_lines: list[str] = []
    for line in content.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith(comment_prefix):
            header_lines.append(stripped.lstrip(comment_prefix + " ").strip())
        else:
            body_lines.append(line)

    return header_lines, "\n".join(body_lines).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", default=REPO_ROOT / "outputs" / "_state.json", type=Path)
    parser.add_argument(
        "--query-dir",
        default=REPO_ROOT / "sparql-queries",
        type=Path,
        help="Directory containing .rq or .gql files.",
    )
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

    # Accept both .rq and .gql files
    query_files = sorted(args.query_dir.glob("*.rq")) + sorted(args.query_dir.glob("*.gql"))
    if not query_files:
        print(f"No .rq or .gql files in {args.query_dir}")
        sys.exit(0)

    query_type = "GQL" if any(f.suffix == ".gql" for f in query_files) else "SPARQL"
    print(f"\nExecuting {len(query_files)} {query_type} queries...\n")
    per_query: list[dict] = []
    passed = failed = 0

    for qf in query_files:
        header_lines, query = _parse_query_file(qf)

        print(f"--- {qf.name} ---")
        for h in header_lines:
            print(f"  {h}")
        print()

        record: dict = {"file": qf.name, "headers": header_lines, "query": query}
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
        "queryType": query_type,
        "passed": passed,
        "failed": failed,
        "total": len(query_files),
        "queries": per_query,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(f"Results: {passed} passed, {failed} failed out of {len(query_files)}  -> {args.out}")


if __name__ == "__main__":
    main()
