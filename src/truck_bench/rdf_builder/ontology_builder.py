"""Generate and validate an OWL ontology (Turtle) from a ParsedOntology."""

from __future__ import annotations

from ..markdown_parser.model import ParsedOntology

_DATA_NS = "http://demo.openlinksw.com/trucking-ontology-benchmark#"
_ONTOLOGY_NS = "http://www.openlinksw.com/ontology/trucking-ontology#"

_MARKDOWN_TO_XSD = {
    "string": "xsd:string",
    "int": "xsd:integer",
    "integer": "xsd:integer",
    "bigint": "xsd:integer",
    "long": "xsd:integer",
    "float": "xsd:double",
    "double": "xsd:double",
    "decimal": "xsd:double",
    "numeric": "xsd:double",
    "boolean": "xsd:boolean",
    "bool": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "timestamp": "xsd:dateTime",
    "uuid": "xsd:string",
    "string[]": "xsd:string",
    "int[]": "xsd:string",
}


def to_camel_case(snake: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _snake_to_label(snake: str) -> str:
    """Convert snake_case to a human-readable label (underscores → spaces)."""
    return snake.replace("_", " ")


def _xsd_type(raw_type: str) -> str:
    return _MARKDOWN_TO_XSD.get(raw_type.lower().split("(")[0].strip(), "xsd:string")


def build_owl_ontology(
    parsed: ParsedOntology,
    *,
    data_ns: str = _DATA_NS,
    ontology_ns: str = _ONTOLOGY_NS,
) -> str:
    """Return the OWL ontology as a Turtle string."""
    lines: list[str] = []

    def _emit(line: str = "") -> None:
        lines.append(line)

    # Prefix declarations
    _emit(f"@prefix : <{data_ns}> .")
    _emit(f"@prefix trucking-ontology: <{ontology_ns}> .")
    _emit("@prefix owl: <http://www.w3.org/2002/07/owl#> .")
    _emit("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    _emit("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    _emit("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    _emit()

    # Ontology header
    rel_count = sum(1 for e in parsed.entities for f in e.fields if f.references_entity)
    _emit(f"<{ontology_ns}ontology> rdf:type owl:Ontology ;")
    _emit(f'    rdfs:label {_turtle_string(parsed.title, lang="en")} ;')
    _emit(
        f'    rdfs:comment {_turtle_string(f"Long-haul trucking ontology — {len(parsed.entities)} entities with {rel_count} relationships", lang="en")} .'
    )
    _emit()

    visited_datatype_props: set[str] = set()
    visited_object_props: set[str] = set()

    for entity in parsed.entities:
        ename = entity.name

        # Class declaration
        _emit(f"trucking-ontology:{ename} rdf:type owl:Class ;")
        _emit(f'    rdfs:label {_turtle_string(ename, lang="en")}')
        if entity.description:
            _emit(f'    ; rdfs:comment {_turtle_string(entity.description, lang="en")}')
        _emit("    .")
        _emit()

        # Properties for each non-PK field
        for field in entity.fields:
            if field.is_primary_key:
                continue

            if field.references_entity:
                # FK → owl:ObjectProperty — strip _id suffix
                base = field.name[:-3] if field.name.endswith("_id") else field.name
                prop_name = to_camel_case(base)
            else:
                prop_name = to_camel_case(field.name)
            label = _snake_to_label(field.name)
            desc = field.description

            if field.references_entity:
                target = field.references_entity

                if prop_name not in visited_object_props:
                    visited_object_props.add(prop_name)
                    _emit(f"trucking-ontology:{prop_name} rdf:type rdf:Property , owl:ObjectProperty ;")
                    _emit(f'    rdfs:label {_turtle_string(label, lang="en")} ;')
                    if desc:
                        _emit(f'    rdfs:comment {_turtle_string(desc, lang="en")} ;')
                    _emit(f"    rdfs:domain trucking-ontology:{ename} ;")
                    _emit(f"    rdfs:range trucking-ontology:{target} .")
                    _emit()
            else:
                # Data field → owl:DatatypeProperty
                xsd_t = _xsd_type(field.raw_type)

                if prop_name not in visited_datatype_props:
                    visited_datatype_props.add(prop_name)
                    _emit(f"trucking-ontology:{prop_name} rdf:type rdf:Property , owl:DatatypeProperty ;")
                    _emit(f'    rdfs:label {_turtle_string(label, lang="en")} ;')
                    if desc:
                        _emit(f'    rdfs:comment {_turtle_string(desc, lang="en")} ;')
                    _emit(f"    rdfs:domain trucking-ontology:{ename} ;")
                    _emit(f"    rdfs:range {xsd_t} .")
                    _emit()

    return "\n".join(lines) + "\n"


def _turtle_string(s: str, lang: str | None = None) -> str:
    """Escape a string for a Turtle quoted literal, with optional language tag."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    if lang:
        return f'"{escaped}"@{lang}'
    return f'"{escaped}"'


def validate_ttl(ttl_text: str) -> dict:
    """Parse the Turtle with rdflib and return validation info.

    Returns a dict with:
        valid: bool
        triples: int (total parsed triples)
        classes: list[str]
        object_properties: list[str]
        datatype_properties: list[str]
        warnings: list[str]
        errors: list[str]
    """
    import rdflib
    from rdflib.namespace import OWL, RDF, RDFS

    g = rdflib.Graph()
    result: dict = {
        "valid": True,
        "triples": 0,
        "classes": [],
        "object_properties": [],
        "datatype_properties": [],
        "warnings": [],
        "errors": [],
    }

    try:
        g.parse(data=ttl_text, format="turtle")
    except Exception as exc:
        result["valid"] = False
        result["errors"].append(f"Parse error: {exc}")
        return result

    result["triples"] = len(g)

    ONT_NS = rdflib.Namespace(_ONTOLOGY_NS)

    # Count classes
    for s in g.subjects(RDF.type, OWL.Class):
        if s.startswith(ONT_NS):
            result["classes"].append(str(s))

    # Count object properties
    for s in g.subjects(RDF.type, OWL.ObjectProperty):
        result["object_properties"].append(str(s))

    # Count datatype properties
    for s in g.subjects(RDF.type, OWL.DatatypeProperty):
        result["datatype_properties"].append(str(s))

    # Warn about properties without domain or range
    for s in set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty)):
        has_domain = next(g.objects(s, RDFS.domain), None) is not None
        has_range = next(g.objects(s, RDFS.range), None) is not None
        if not has_domain:
            result["warnings"].append(f"Property {s} has no rdfs:domain")
        if not has_range:
            result["warnings"].append(f"Property {s} has no rdfs:range")

    # Warn about classes or properties without rdfs:label
    for s in g.subjects(RDF.type, OWL.Class):
        if next(g.objects(s, RDFS.label), None) is None:
            result["warnings"].append(f"Class {s} has no rdfs:label")
    for s in set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty)):
        if next(g.objects(s, RDFS.label), None) is None:
            result["warnings"].append(f"Property {s} has no rdfs:label")

    return result
