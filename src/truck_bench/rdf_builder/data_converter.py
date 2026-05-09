"""Convert JSONL seed files to RDF Turtle instances."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..markdown_parser.model import ParsedOntology
from .ontology_builder import _xsd_type, to_camel_case

_DATA_NS = "http://demo.openlinksw.com/trucking-ontology-benchmark#"
_ONTOLOGY_NS = "http://www.openlinksw.com/ontology/trucking-ontology#"


def _snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", s)
    return s.lower()


def _pluralize(snake: str) -> str:
    irregular = {
        "maintenance_event": "maintenance_events",
        "service_ticket": "service_tickets",
        "driver_hos_log": "driver_hos_logs",
    }
    if snake in irregular:
        return irregular[snake]
    if snake.endswith("y"):
        return snake[:-1] + "ies"
    if snake.endswith("s") or snake.endswith("x"):
        return snake + "es"
    return snake + "s"


def _entity_seed_file(entity_name: str) -> str:
    return f"{_pluralize(_snake(entity_name))}.jsonl"


def _turtle_literal(value, raw_type: str) -> str | None:
    """Format a value as a Turtle literal. Returns None for null/missing values."""
    if value is None:
        return None

    core = raw_type.lower().split("(")[0].strip()

    if core in ("boolean", "bool"):
        return "true" if value is True else "false"

    if core in ("int", "integer", "bigint", "long"):
        return str(int(value))

    if core in ("float", "double", "decimal", "numeric"):
        return str(float(value))

    if core == "date":
        return f'"{value}"^^xsd:date'

    if core in ("datetime", "timestamp"):
        return f'"{value}"^^xsd:dateTime'

    # string, uuid, string[], int[], and fallback
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def convert_jsonl_to_turtle(
    parsed: ParsedOntology,
    seed_dir: Path,
    *,
    data_ns: str = _DATA_NS,
    ontology_ns: str = _ONTOLOGY_NS,
) -> str:
    """Convert all JSONL seed files to a Turtle string of instance triples.

    Returns the Turtle text containing all instances across all entities.
    """
    lines: list[str] = []

    def _emit(line: str = "") -> None:
        lines.append(line)

    _emit(f"@prefix : <{data_ns}> .")
    _emit(f"@prefix trucking-ontology: <{ontology_ns}> .")
    _emit("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    _emit("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    _emit()

    for entity in parsed.entities:
        ename = entity.name
        entity_lower = ename.lower()
        pk_field = entity.primary_key
        if not pk_field:
            print(f"  WARNING: {ename} has no PK — skipping")
            continue
        pk_name = pk_field[0]

        seed_file = _entity_seed_file(ename)
        seed_path = seed_dir / seed_file

        if not seed_path.exists():
            print(f"  WARNING: {seed_file} not found — skipping {ename}")
            continue

        # Build FK → target entity lookup
        fk_targets: dict[str, str] = {}
        for f in entity.fields:
            if f.references_entity:
                fk_targets[f.name] = f.references_entity.lower()

        # Build field → raw_type lookup
        field_types: dict[str, str] = {f.name: f.raw_type for f in entity.fields}

        row_count = 0
        for line in seed_path.read_text(encoding="utf-8").strip().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            pk_val = row.get(pk_name)
            if pk_val is None:
                continue

            instance = f":{entity_lower}-{pk_val}"

            # rdf:type
            _emit(f"{instance} rdf:type trucking-ontology:{ename} .")

            for field in entity.fields:
                fname = field.name
                if field.is_primary_key:
                    continue

                val = row.get(fname)
                if val is None:
                    continue

                if field.references_entity:
                    base = fname[:-3] if fname.endswith("_id") else fname
                    prop = f"trucking-ontology:{to_camel_case(base)}"
                else:
                    prop = f"trucking-ontology:{to_camel_case(fname)}"

                if field.references_entity:
                    target_lower = fk_targets[fname]
                    _emit(f"{instance} {prop} :{target_lower}-{val} .")
                elif isinstance(val, list):
                    # Native JSON array — emit one object per element
                    if val:
                        values = []
                        for item in val:
                            escaped = str(item).replace("\\", "\\\\").replace('"', '\\"')
                            values.append(f'"{escaped}"')
                        joined = ", ".join(values)
                        _emit(f"{instance} {prop} {joined} .")
                elif isinstance(val, str) and val.strip().startswith("["):
                    # JSON-encoded array (e.g. waypoints) — emit as RDF collection
                    parsed = json.loads(val)
                    if isinstance(parsed, list) and parsed:
                        elements = []
                        for item in parsed:
                            escaped = str(item).replace("\\", "\\\\").replace('"', '\\"')
                            elements.append(f'"{escaped}"')
                        joined = " ".join(elements)
                        _emit(f"{instance} {prop} ({joined}) .")
                else:
                    rtype = field_types.get(fname, "string")
                    lit = _turtle_literal(val, rtype)
                    if lit is not None:
                        _emit(f"{instance} {prop} {lit} .")

            row_count += 1
            _emit()

        print(f"  {ename}: {row_count} instances from {seed_file}")

    return "\n".join(lines) + "\n"
