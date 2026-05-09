"""04 - Load ontology and data TTLs into Virtuoso.

Inputs:
    outputs/truck_ontology.ttl
    outputs/truck_data.ttl
Outputs:
    outputs/_state.json
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
    parser.add_argument(
        "--ontology",
        default=REPO_ROOT / "outputs" / "truck_ontology.ttl",
        type=Path,
    )
    parser.add_argument(
        "--data",
        default=REPO_ROOT / "outputs" / "truck_data.ttl",
        type=Path,
    )
    parser.add_argument(
        "--state-out",
        default=REPO_ROOT / "outputs" / "_state.json",
        type=Path,
    )
    parser.add_argument(
        "--graph-uri",
        default=None,
        help="Override the named graph URI (default from .env).",
    )
    args = parser.parse_args()

    config = VirtuosoConfig.from_env()
    client = SparqlClient(config)
    graph_uri = args.graph_uri or config.graph_uri

    if not args.ontology.exists():
        print(f"Missing: {args.ontology} — run scripts/02_build_rdf.py first")
        sys.exit(1)
    if not args.data.exists():
        print(f"Missing: {args.data} — run scripts/03_convert_data.py first")
        sys.exit(1)

    print(f"Virtuoso: {config.sparql_url}")
    print(f"Graph:    {graph_uri}")

    # Verify connectivity
    try:
        rows = client.execute_select("SELECT 1 AS ?ping WHERE {}")
        assert rows == [{"ping": "1"}]
        print("  connection OK")
    except Exception as exc:
        print(f"  connection FAILED: {exc}")
        sys.exit(1)

    # Clear + load
    print(f"\nClearing graph <{graph_uri}> ...")
    client.clear_graph(graph_uri)

    # Concatenate both TTLs so they load atomically (graph-crud PUT replaces)
    combined = args.ontology.read_text(encoding="utf-8") + "\n" + args.data.read_text(encoding="utf-8")
    print(f"\nLoading {args.ontology.name} + {args.data.name} ...")
    client.load_ttl_graph_crud(combined, graph_uri)

    # Verify
    count = client.count_triples(graph_uri)
    print(f"\n  Triples in graph: {count}")

    if count < 1000:
        print("  WARNING: very few triples loaded — the Virtuoso endpoint may not have accepted the load")
    else:
        print("  Load appears healthy.")

    # State
    state = {
        "virtuosoHost": config.host,
        "virtuosoPort": config.port,
        "graphUri": graph_uri,
        "tripleCount": count,
    }
    args.state_out.parent.mkdir(parents=True, exist_ok=True)
    args.state_out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nState → {args.state_out}")


if __name__ == "__main__":
    main()
