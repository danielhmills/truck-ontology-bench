"""Virtuoso SPARQL endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _walk_and_load_env(start: Path | None = None) -> None:
    start = start or Path.cwd()
    for parent in [start, *start.parents]:
        env_file = parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            return
    load_dotenv()


@dataclass(frozen=True)
class VirtuosoConfig:
    host: str = "localhost"
    port: int = 8890
    user: str = "dba"
    password: str = "dba"
    sparql_endpoint: str = "/sparql"
    graph_uri: str | None = "http://demo.openlinksw.com/trucking-ontology-benchmark/graph"

    @property
    def sparql_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.sparql_endpoint}"

    @classmethod
    def from_env(cls, start: Path | None = None) -> VirtuosoConfig:
        if start is None:
            start = Path(__file__).resolve().parent
        _walk_and_load_env(start)

        graph = os.environ.get("VIRTUOSO_GRAPH_URI") or None

        return cls(
            host=os.environ.get("VIRTUOSO_HOST", "localhost"),
            port=int(os.environ.get("VIRTUOSO_PORT", "8890")),
            user=os.environ.get("VIRTUOSO_USER", "dba"),
            password=os.environ.get("VIRTUOSO_PASSWORD", "dba"),
            sparql_endpoint=os.environ.get("VIRTUOSO_SPARQL_ENDPOINT", "/sparql"),
            graph_uri=graph,
        )
