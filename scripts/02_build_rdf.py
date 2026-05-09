"""02 - Build the OWL ontology (Turtle) from the parsed Markdown ontology.

Inputs:
    input/schema/ontology.md
Outputs:
    outputs/truck_ontology.ttl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.markdown_parser import parse_markdown  # noqa: E402
from truck_bench.rdf_builder import build_owl_ontology, validate_ttl  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--md",
        default=REPO_ROOT / "input" / "schema" / "ontology.md",
        type=Path,
        help="Path to the Markdown ontology spec.",
    )
    parser.add_argument(
        "--out",
        default=REPO_ROOT / "outputs" / "truck_ontology.ttl",
        type=Path,
        help="Path for the generated OWL Turtle file.",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip rdflib validation of the generated Turtle.",
    )
    args = parser.parse_args()

    print(f"Parsing {args.md} ...")
    parsed = parse_markdown(args.md)
    print(parsed.summary)

    print("\nBuilding OWL ontology...")
    ttl = build_owl_ontology(parsed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(ttl, encoding="utf-8")

    # Quick stats from the source
    classes = len(parsed.entities)
    obj_props = sum(
        1 for e in parsed.entities for f in e.fields if f.references_entity
    )
    data_props = sum(
        1
        for e in parsed.entities
        for f in e.fields
        if not f.is_primary_key and not f.references_entity
    )
    print(f"\nOntology written to {args.out}")
    print(f"  Source:          {classes} classes, {obj_props} FKs, {data_props} data fields")

    if not args.no_validate:
        print("\nValidating with rdflib...")
        result = validate_ttl(ttl)

        if not result["valid"]:
            print("  FAILED — Turtle parse errors:")
            for err in result["errors"]:
                print(f"    {err}")
            sys.exit(1)

        print(f"  Valid:           yes ({result['triples']} triples parsed)")
        print(f"  owl:Class:           {len(result['classes'])}")
        print(f"  owl:ObjectProperty:  {len(result['object_properties'])}")
        print(f"  owl:DatatypeProperty: {len(result['datatype_properties'])}")

        if result["warnings"]:
            print(f"\n  Warnings ({len(result['warnings'])}):")
            for w in result["warnings"]:
                print(f"    - {w}")


if __name__ == "__main__":
    main()
