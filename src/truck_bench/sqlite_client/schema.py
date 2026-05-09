"""Auto-generate SQLite CREATE TABLE statements from a ParsedOntology."""

from __future__ import annotations

import re

from ..markdown_parser.model import ParsedOntology

_MD_TO_SQL = {
    "string": "TEXT",
    "uuid": "TEXT",
    "boolean": "INTEGER",
    "bool": "INTEGER",
    "int": "INTEGER",
    "integer": "INTEGER",
    "bigint": "INTEGER",
    "long": "INTEGER",
    "float": "REAL",
    "double": "REAL",
    "decimal": "REAL",
    "numeric": "REAL",
    "date": "TEXT",
    "datetime": "TEXT",
    "timestamp": "TEXT",
    "string[]": "TEXT",
    "int[]": "TEXT",
}


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


def _sql_type(raw_type: str) -> str:
    core = raw_type.lower().split("(")[0].strip()
    return _MD_TO_SQL.get(core, "TEXT")


def entity_table_name(entity_name: str) -> str:
    return _pluralize(_snake(entity_name))


def build_create_tables(parsed: ParsedOntology) -> str:
    """Return DDL statements for all 11 entities."""
    stmts: list[str] = []
    for entity in parsed.entities:
        table = entity_table_name(entity.name)
        cols: list[str] = []
        for f in entity.fields:
            sql_t = _sql_type(f.raw_type)
            if f.is_primary_key:
                cols.append(f"  {f.name} {sql_t} PRIMARY KEY")
            else:
                cols.append(f"  {f.name} {sql_t}")
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(cols) + "\n);")
    return "\n\n".join(stmts) + "\n"
