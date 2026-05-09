"""SPARQL protocol client for Virtuoso."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .config import VirtuosoConfig


class SparqlClient:
    """Thin client for Virtuoso's /sparql endpoint."""

    def __init__(self, config: VirtuosoConfig | None = None):
        self.config = config or VirtuosoConfig.from_env()

    # -- SELECT -----------------------------------------------------------

    def _post(self, query: str, accept: str, timeout: int = 60) -> requests.Response:
        return requests.post(
            self.config.sparql_url,
            data={"query": query},
            headers={"Accept": accept},
            auth=(self.config.user, self.config.password),
            timeout=timeout,
        )

    def execute_query(self, query: str) -> dict[str, Any]:
        """Execute SELECT/ASK and return parsed JSON."""
        resp = self._post(query, "application/sparql-results+json")
        resp.raise_for_status()
        return resp.json()

    def execute_select(self, query: str) -> list[dict[str, Any]]:
        """Execute SELECT, returning simplified binding dicts (var→value)."""
        raw = self.execute_query(query)
        bindings = raw.get("results", {}).get("bindings", [])
        return [{k: v.get("value", "") for k, v in b.items()} for b in bindings]

    def execute_ask(self, query: str) -> bool:
        """Execute ASK query."""
        raw = self.execute_query(query)
        return raw.get("boolean", False)

    # -- UPDATE -----------------------------------------------------------

    def update(self, query: str, timeout: int = 120) -> None:
        """Execute SPARQL UPDATE (INSERT DATA, DELETE, CLEAR)."""
        resp = self._post(query, "application/sparql-results+json", timeout=timeout)
        resp.raise_for_status()

    # -- Graph management --------------------------------------------------

    def load_ttl(self, ttl_text: str, graph_uri: str | None = None) -> None:
        """Load Turtle text into a named graph via SPARQL INSERT DATA."""
        g = graph_uri or self.config.graph_uri
        if not g:
            raise ValueError("graph_uri is required for load_ttl")
        escaped = ttl_text.replace("\\", "\\\\")
        insert = f"INSERT DATA {{ GRAPH <{g}> {{ {escaped} }} }}"
        self.update(insert, timeout=300)

    def load_file(self, path: Path, graph_uri: str | None = None) -> None:
        """Load a Turtle file into Virtuoso."""
        ttl = path.read_text(encoding="utf-8")
        self.load_ttl(ttl, graph_uri)

    def clear_graph(self, graph_uri: str | None = None) -> None:
        """Clear all triples from a named graph (or the default graph)."""
        g = graph_uri or self.config.graph_uri
        if g:
            self.update(f"CLEAR GRAPH <{g}>")
        else:
            self.update("CLEAR DEFAULT")

    def count_triples(self, graph_uri: str | None = None) -> int:
        """Count triples in the given graph (or default)."""
        g = graph_uri or self.config.graph_uri
        if g:
            q = f"SELECT (COUNT(*) AS ?cnt) WHERE {{ GRAPH <{g}> {{ ?s ?p ?o }} }}"
        else:
            q = "SELECT (COUNT(*) AS ?cnt) WHERE { ?s ?p ?o }"
        rows = self.execute_select(q)
        return int(rows[0]["cnt"]) if rows else 0
