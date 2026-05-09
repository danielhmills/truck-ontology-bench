"""03 - Convert JSONL seed data to RDF Turtle instances.

Inputs:
    input/schema/ontology.md
    input/data/seed/*.jsonl
Outputs:
    outputs/truck_data.ttl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.markdown_parser import parse_markdown  # noqa: E402
from truck_bench.rdf_builder import convert_jsonl_to_turtle, validate_ttl  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--md",
        default=REPO_ROOT / "input" / "schema" / "ontology.md",
        type=Path,
        help="Path to the Markdown ontology spec.",
    )
    parser.add_argument(
        "--seed-dir",
        default=REPO_ROOT / "input" / "data" / "seed",
        type=Path,
        help="Directory containing the JSONL seed files.",
    )
    parser.add_argument(
        "--out",
        default=REPO_ROOT / "outputs" / "truck_data.ttl",
        type=Path,
        help="Path for the generated Turtle data file.",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip rdflib validation of the generated Turtle.",
    )
    args = parser.parse_args()

    print(f"Parsing {args.md} ...")
    parsed = parse_markdown(args.md)

    print(f"\nConverting JSONL to Turtle...")
    ttl = convert_jsonl_to_turtle(parsed, args.seed_dir)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(ttl, encoding="utf-8")
    print(f"\nData written to {args.out}")

    if not args.no_validate:
        print("\nValidating with rdflib...")
        result = validate_ttl(ttl)

        if not result["valid"]:
            print("  FAILED — Turtle parse errors:")
            for err in result["errors"]:
                print(f"    {err}")
            sys.exit(1)

        print(f"  Valid:  yes ({result['triples']} triples)")
        if result["warnings"]:
            for w in result["warnings"]:
                print(f"  WARN:   {w}")


if __name__ == "__main__":
    main()
