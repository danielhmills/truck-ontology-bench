"""LangChain agent runner (replaces Fabric Data Agent provisioning)."""

from .provision import upsert_naked_agent, upsert_ontology_agent
from .runner import evaluate_answer, run_benchmark

__all__ = ["evaluate_answer", "run_benchmark", "upsert_naked_agent", "upsert_ontology_agent"]
