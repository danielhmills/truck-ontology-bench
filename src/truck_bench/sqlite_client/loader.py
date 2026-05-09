"""Load JSONL seed data into SQLite."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..markdown_parser.model import ParsedOntology
from .schema import _pluralize, _snake, build_create_tables


def _seed_filename(entity_name: str) -> str:
    return f"{_pluralize(_snake(entity_name))}.jsonl"


def load_jsonl_to_sqlite(
    db_path: Path | str,
    seed_dir: Path,
    parsed: ParsedOntology,
) -> dict[str, int]:
    """Create tables and load all JSONL seed data into SQLite.

    Returns a dict mapping entity name → row count.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Drop existing tables so re-runs start fresh
    for entity in parsed.entities:
        table = _pluralize(_snake(entity.name))
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    ddl = build_create_tables(parsed)
    conn.executescript(ddl)

    counts: dict[str, int] = {}
    for entity in parsed.entities:
        table = _pluralize(_snake(entity.name))
        seed_file = _seed_filename(entity.name)
        seed_path = seed_dir / seed_file
        if not seed_path.exists():
            print(f"  WARNING: {seed_file} not found — skipping {entity.name}")
            counts[entity.name] = 0
            continue

        col_names = [f.name for f in entity.fields]
        placeholders = ", ".join("?" for _ in col_names)
        insert_sql = f"INSERT INTO {table} ({', '.join(col_names)}) VALUES ({placeholders})"

        count = 0
        for line in seed_path.read_text(encoding="utf-8").strip().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            values = []
            for f in entity.fields:
                val = row.get(f.name)
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                values.append(val)
            conn.execute(insert_sql, values)
            count += 1

        counts[entity.name] = count
        print(f"  {entity.name}: {count} rows → {table}")

    conn.commit()
    conn.close()
    return counts
