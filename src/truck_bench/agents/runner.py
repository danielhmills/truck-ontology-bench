"""LangChain agent runner — replaces Fabric Data Agent provisioning + notebook.

Provides two agents (OntologyAgent via SPARQL, NakedAgent via SQL) backed
by the same LLM. The benchmark loop runs all 18 scenarios and writes
``_agent_comparison.json`` in the format ``06_score.py`` expects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .instructions import NAKED_AGENT_INSTRUCTIONS, ONTOLOGY_AGENT_INSTRUCTIONS


def _normalize(text: str) -> str:
    """Fold separators to a single space for token matching."""
    return re.sub(r"[_\-\s/]+", " ", text).lower().strip()


def evaluate_answer(
    answer: str,
    signals: list[str],
) -> tuple[bool, list[str], list[str]]:
    """Token-match ontology signals against an agent answer.

    Returns (all_matched, matched_signals, missing_signals).
    """
    if not signals:
        return True, [], []

    norm = _normalize(answer)
    matched = [s for s in signals if _normalize(s) in norm]
    missing = [s for s in signals if s not in matched]
    return len(missing) == 0, matched, missing


def _make_sparql_tool(sparql_query_fn):
    """Create a SPARQL query tool bound to the given client function."""
    from langchain_core.tools import tool

    @tool
    def query_sparql(query: str) -> str:
        """Run a SPARQL SELECT query against the truck ontology graph.

        The graph uses these prefixes:
          PREFIX trucking-ontology: <http://www.openlinksw.com/ontology/trucking-ontology#>
          PREFIX : <http://demo.openlinksw.com/trucking-ontology-benchmark#>

        Entity classes are trucking-ontology:Terminal, trucking-ontology:Truck, etc.
        Object properties are camelCase with _id stripped (e.g. trucking-ontology:driver).
        Data properties are camelCase (e.g. trucking-ontology:truckNumber).

        Args:
            query: The SPARQL SELECT query string.
        """
        try:
            rows = sparql_query_fn(query)
            return json.dumps(rows, indent=2, ensure_ascii=False)
        except Exception as exc:
            return f"SPARQL error: {exc}"

    return query_sparql


def _make_sql_tool(db_path: str):
    """Create a SQL query tool bound to the SQLite database."""
    from langchain_core.tools import tool

    @tool
    def query_sql(query: str) -> str:
        """Run a SQL query against the trucking fleet SQLite database.

        The database has 11 tables: terminals, trucks, trailers, drivers,
        customers, routes, loads, trips, maintenance_events, service_tickets,
        driver_hos_logs.

        All PKs and FKs follow snake_case naming with _id suffixes
        (e.g. terminal_id, home_terminal_id).

        Args:
            query: The SQL SELECT query string.
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(query)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"SQL error: {exc}"

    return query_sql


def _create_agent_executor(llm, tool, system_message: str, max_iterations: int = 5):
    """Build a LangChain tool-calling agent with a single tool."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    # Escape curly braces that LangChain would misinterpret as f-string variables
    escaped = system_message.replace("{", "{{").replace("}", "}}")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", escaped),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, [tool], prompt)
    return AgentExecutor(
        agent=agent,
        tools=[tool],
        max_iterations=max_iterations,
        verbose=False,
        handle_parsing_errors=True,
    )


def _build_llm():
    """Create an LLM from environment variables.

    Controlled by:
      LLM_PROVIDER     — openai (default), anthropic, or custom
      LLM_MODEL_NAME   — model name (default: gpt-4o-mini)
      LLM_BASE_URL     — override the API base URL (Ollama, vLLM, LiteLLM, etc.)

    For custom providers, uses the OpenAI-compatible chat completions
    endpoint (most OSS model servers speak this protocol).
    """
    provider = os.environ.get("LLM_PROVIDER", "openai").lower().strip()
    model = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
    base_url = os.environ.get("LLM_BASE_URL") or None

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {"model": model, "temperature": 0}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs)

    # openai or custom (both use OpenAI-compatible API)
    from langchain_openai import ChatOpenAI

    kwargs = {"model": model, "temperature": 0}
    if base_url:
        kwargs["base_url"] = base_url
    if provider == "custom":
        kwargs["openai_api_key"] = os.environ.get("OPENAI_API_KEY", "not-needed")
    return ChatOpenAI(**kwargs)


def run_benchmark(
    *,
    scenarios: list[dict],
    db_path: str,
    sparql_query_fn,
    llm=None,
    max_iterations: int = 5,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run all 18 scenarios through both agents.

    Returns the ``_agent_comparison.json`` dict.
    """
    if llm is None:
        llm = _build_llm()

    sparql_tool = _make_sparql_tool(sparql_query_fn)
    sql_tool = _make_sql_tool(db_path)

    ontology_executor = _create_agent_executor(
        llm, sparql_tool, ONTOLOGY_AGENT_INSTRUCTIONS, max_iterations=max_iterations
    )
    naked_executor = _create_agent_executor(
        llm, sql_tool, NAKED_AGENT_INSTRUCTIONS, max_iterations=max_iterations
    )

    # Canonical scenarios hash for reproducibility tracking
    scenarios_json = json.dumps(scenarios, sort_keys=True, separators=(",", ":"))
    scenarios_sha256 = hashlib.sha256(scenarios_json.encode()).hexdigest()

    per_question: list[dict] = []
    naked_correct = ontology_correct = 0
    naked_scored = ontology_scored = 0

    for scenario in scenarios:
        sid = scenario["scenario_id"]
        question = scenario["user_question"]
        signals = scenario.get("ontology_signals", [])

        print(f"\n{'=' * 60}")
        print(f"  {sid} — {scenario['domain']}")
        print(f"  {question}")
        print(f"  signals: {signals}")
        print(f"{'=' * 60}")

        row: dict = {
            "scenario_id": sid,
            "domain": scenario.get("domain", ""),
            "question": question,
            "expected_answer": scenario.get("gold_label", ""),
            "ontology_signals": signals,
        }

        # NakedAgent
        print(f"\n  [NakedAgent]")
        try:
            result = naked_executor.invoke(
                {"input": question},
                config={"timeout": timeout_seconds},
            )
            naked_answer = result.get("output", "")
        except Exception as exc:
            naked_answer = f"<error: {exc}>"
        print(f"    {naked_answer[:200]}{'...' if len(naked_answer) > 200 else ''}")

        naked_ok, naked_matched, naked_missing = evaluate_answer(naked_answer, signals)
        row.update({
            "actual_answer_naked": naked_answer,
            "evaluation_judgement_naked": naked_ok,
            "matched_signals_naked": naked_matched,
            "missing_signals_naked": naked_missing,
        })
        if signals:
            naked_scored += 1
            if naked_ok:
                naked_correct += 1

        # OntologyAgent
        print(f"\n  [OntologyAgent]")
        try:
            result = ontology_executor.invoke(
                {"input": question},
                config={"timeout": timeout_seconds},
            )
            ontology_answer = result.get("output", "")
        except Exception as exc:
            ontology_answer = f"<error: {exc}>"
        print(f"    {ontology_answer[:200]}{'...' if len(ontology_answer) > 200 else ''}")

        ontology_ok, ontology_matched, ontology_missing = evaluate_answer(
            ontology_answer, signals
        )
        row.update({
            "actual_answer_ontology": ontology_answer,
            "evaluation_judgement_ontology": ontology_ok,
            "matched_signals_ontology": ontology_matched,
            "missing_signals_ontology": ontology_missing,
        })
        if signals:
            ontology_scored += 1
            if ontology_ok:
                ontology_correct += 1

        per_question.append(row)

    total = len(scenarios)
    return {
        "runAtUtc": datetime.now(timezone.utc).isoformat(),
        "scoringMethod": "ontology_signals token match (langchain + virtuoso)",
        "scenariosSha256": scenarios_sha256,
        "scenariosPayload": scenarios,
        "agents": {
            "naked": {
                "name": "NakedAgent",
                "correctCount": naked_correct,
                "scoredQuestions": naked_scored,
                "naQuestions": total - naked_scored,
                "totalQuestions": total,
                "accuracyPct": round(100 * naked_correct / naked_scored, 1)
                if naked_scored
                else 0,
            },
            "ontology": {
                "name": "OntologyAgent",
                "correctCount": ontology_correct,
                "scoredQuestions": ontology_scored,
                "naQuestions": total - ontology_scored,
                "totalQuestions": total,
                "accuracyPct": round(100 * ontology_correct / ontology_scored, 1)
                if ontology_scored
                else 0,
            },
        },
        "perQuestion": per_question,
    }
