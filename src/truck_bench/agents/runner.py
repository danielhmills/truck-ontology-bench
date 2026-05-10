"""Agent runner — replaces Fabric Data Agent provisioning + notebook.

Provides two agents (OntologyAgent via SPARQL, NakedAgent via SQL) backed
by the same LLM.  Uses the OpenAI Python client directly so that
``extra_body`` (thinking mode control, custom params) works for
providers like DeepSeek.  The benchmark loop runs all 18 scenarios and
writes ``_agent_comparison.json`` in the format ``07_score.py`` expects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .instructions import (
    NAKED_AGENT_INSTRUCTIONS,
    ONTOLOGY_AGENT_INSTRUCTIONS_GQL,
)


def _normalize(text: str) -> str:
    t = str(text)
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = t.lower()
    t = re.sub(r"[_\-\s/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_text(answer) -> str:
    """Extract plain text from various LLM response formats."""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, list):
        # Anthropic content blocks: [{"type": "text", "text": "..."}, ...]
        parts = []
        for block in answer:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
                elif block.get("type") == "thinking":
                    pass  # skip thinking blocks
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(answer)


def evaluate_answer(
    answer: str,
    signals: list[str],
) -> tuple[bool, list[str], list[str]]:
    if not signals:
        return True, [], []
    norm = _normalize(answer)
    matched = [s for s in signals if _normalize(s) in norm]
    missing = [s for s in signals if s not in matched]
    return len(missing) == 0, matched, missing


# -- Tool implementations --------------------------------------------------

_SPARQL_DETECT = re.compile(r"^\s*(PREFIX|SELECT\s|ASK\s|CONSTRUCT\s|DESCRIBE\s)", re.IGNORECASE | re.MULTILINE)


def _run_sparql(query: str, sparql_query_fn) -> str:
    if _SPARQL_DETECT.search(query):
        return (
            "ERROR: You sent SPARQL, but this tool only accepts GQL (Graph Query Language).\n"
            "Rewrite your query in GQL format. Every GQL query must start with:\n"
            "  GQL\n"
            "  BASE <http://www.openlinksw.com/ontology/trucking-ontology#>\n"
            "  USE GRAPH <http://demo.openlinksw.com/trucking-ontology-benchmark/graph>\n"
            "Use ::Type for entity types (not rdf:type), dot notation for properties (t.truckNumber),\n"
            "and -[:trucking:edge]-> for relationships.  Do NOT use PREFIX, SELECT, or ?variables."
        )
    try:
        rows = sparql_query_fn(query)
        return json.dumps(rows, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Query error: {exc}"


def _run_sql(query: str, db_path: str) -> str:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        return f"SQL error: {exc}"


# -- OpenAI tool schemas ----------------------------------------------------

_GQL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_gql",
        "description": (
            "Run a GQL (Graph Query Language) query against the truck ontology graph.\n"
            "Every query MUST start with:\n"
            "  GQL\n"
            "  BASE <http://www.openlinksw.com/ontology/trucking-ontology#>\n"
            "  USE GRAPH <http://demo.openlinksw.com/trucking-ontology-benchmark/graph>\n"
            "Entity types use ::Type syntax: ::Terminal, ::Truck, ::Driver, ::Route, etc.\n"
            "Edges use bracket-arrow with trucking: prefix: -[:trucking:driver]->, -[:trucking:homeTerminal]->\n"
            "Properties use dot notation: t.truckNumber, d.firstName, r.routeName (camelCase).\n"
            "FK edges drop _id: driver_id field -> trucking:driver edge.\n"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The GQL query."},
            },
            "required": ["query"],
        },
    },
}

_SQL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_sql",
        "description": (
            "Run a SQL query against the trucking fleet SQLite database.\n"
            "11 tables: terminals, trucks, trailers, drivers, customers, routes, loads, trips, maintenance_events, service_tickets, driver_hos_logs.\n"
            "All PKs/FKs are snake_case with _id suffixes (e.g. terminal_id, home_terminal_id).\n"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The SQL SELECT query."},
            },
            "required": ["query"],
        },
    },
}


# -- Client + agent loop ---------------------------------------------------

def _build_client():
    """Build an OpenAI client from environment variables."""
    from openai import OpenAI

    provider = os.environ.get("LLM_PROVIDER", "openai").lower().strip()
    model = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
    base_url = os.environ.get("LLM_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY", "not-needed")

    if provider in ("openai", "custom"):
        return (
            OpenAI(base_url=base_url, api_key=api_key) if base_url else OpenAI(api_key=api_key)
        ), model

    if provider == "anthropic":
        # Anthropic uses its own client via langchain — keep LangChain path for this one
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {"model": model, "temperature": 0}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs), model

    return OpenAI(api_key=api_key), model


def _invoke_llm(
    client,
    model: str,
    system_message: str,
    user_question: str,
    tool_schema: list[dict],
    db_path: str,
    sparql_query_fn,
    max_iterations: int = 5,
) -> tuple[str, list[str], list[str]]:
    """Direct agent loop using the OpenAI client.

    Returns (answer_text, list_of_queries_run, list_of_results).

    Preserves reasoning_content in assistant messages so DeepSeek V4
    thinking mode works across multi-turn tool calls.
    """
    provider = os.environ.get("LLM_PROVIDER", "openai").lower().strip()

    # Build extra_body for providers that need it
    extra_body: dict = {}
    extra_raw = os.environ.get("LLM_EXTRA_BODY")
    if extra_raw:
        try:
            extra_body = json.loads(extra_raw)
        except json.JSONDecodeError:
            pass

    messages: list[dict] = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_question},
    ]

    final_answer = ""
    queries: list[str] = []
    query_results: list[str] = []

    for _ in range(max_iterations):
        # LangChain Anthropic path
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            from langchain.agents import AgentExecutor, create_tool_calling_agent
            from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
            from langchain_core.tools import tool as lc_tool

            escaped = system_message.replace("{", "{{").replace("}", "}}")
            prompt = ChatPromptTemplate.from_messages([
                ("system", escaped),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ])

            is_ontology = "sparql" in str(tool_schema) or "gql" in str(tool_schema)

            if is_ontology:

                @lc_tool
                def _t(query: str) -> str:
                    """Run a query against the truck ontology graph."""
                    queries.append(query)
                    r = _run_sparql(query, sparql_query_fn)
                    query_results.append(r)
                    return r
            else:

                @lc_tool
                def _t(query: str) -> str:
                    """Run a SQL query against the trucking fleet database."""
                    queries.append(query)
                    r = _run_sql(query, db_path)
                    query_results.append(r)
                    return r

            agent = create_tool_calling_agent(client, [_t], prompt)
            executor = AgentExecutor(agent=agent, tools=[_t], max_iterations=max_iterations, verbose=False, handle_parsing_errors=True)
            result = executor.invoke({"input": user_question})
            return _extract_text(result.get("output", "")), queries, query_results

        # OpenAI / custom path
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "tools": tool_schema,
        }
        # Force tool call on first turn; allow free-form after data is available
        if len(queries) == 0:
            kwargs["tool_choice"] = "required"
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        # Build assistant message, preserving reasoning_content for DeepSeek
        assistant_msg: dict = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            assistant_msg["reasoning_content"] = msg.reasoning_content

        # Record final answer when the model responds without tool calls
        if msg.content:
            final_answer = msg.content

        if not msg.tool_calls:
            messages.append(assistant_msg)
            break

        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
        messages.append(assistant_msg)

        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
                query_str = args.get("query", "")
            except json.JSONDecodeError:
                query_str = tc.function.arguments

            queries.append(query_str)

            if func_name in ("query_sparql", "query_gql"):
                result = _run_sparql(query_str, sparql_query_fn)
            else:
                result = _run_sql(query_str, db_path)

            query_results.append(result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return final_answer, queries, query_results


# -- Benchmark runner ------------------------------------------------------

def run_benchmark(
    *,
    scenarios: list[dict],
    db_path: str,
    sparql_query_fn,
    max_iterations: int = 5,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run all 18 scenarios through both agents."""

    client, model = _build_client()

    query_lang = "gql"
    onto_instructions = ONTOLOGY_AGENT_INSTRUCTIONS_GQL
    onto_tool_schema = _GQL_TOOL_SCHEMA

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
            "query_language": query_lang,
        }

        # NakedAgent (SQL)
        print(f"\n  [NakedAgent]")
        try:
            naked_answer, naked_queries, naked_results = _invoke_llm(
                client, model, NAKED_AGENT_INSTRUCTIONS, question,
                [_SQL_TOOL_SCHEMA], db_path, sparql_query_fn,
                max_iterations=max_iterations,
            )
        except Exception as exc:
            naked_answer = f"<error: {exc}>"
            naked_queries = []
            naked_results = []
        naked_text = _extract_text(naked_answer)
        print(f"    {naked_text[:200]}{'...' if len(naked_text) > 200 else ''}")

        naked_ok, naked_matched, naked_missing = evaluate_answer(naked_text, signals)
        row.update({
            "actual_answer_naked": naked_text,
            "naked_queries": naked_queries,
            "naked_results": naked_results,
            "evaluation_judgement_naked": naked_ok,
            "matched_signals_naked": naked_matched,
            "missing_signals_naked": naked_missing,
        })
        if signals:
            naked_scored += 1
            if naked_ok:
                naked_correct += 1

        # OntologyAgent (SPARQL)
        print(f"\n  [OntologyAgent]")
        try:
            ontology_answer, ontology_queries, ontology_results = _invoke_llm(
                client, model, onto_instructions, question,
                [onto_tool_schema], db_path, sparql_query_fn,
                max_iterations=max_iterations,
            )
        except Exception as exc:
            ontology_answer = f"<error: {exc}>"
            ontology_queries = []
            ontology_results = []
        ontology_text = _extract_text(ontology_answer)
        print(f"    {ontology_text[:200]}{'...' if len(ontology_text) > 200 else ''}")

        ontology_ok, ontology_matched, ontology_missing = evaluate_answer(ontology_text, signals)
        row.update({
            "actual_answer_ontology": ontology_text,
            "ontology_queries": ontology_queries,
            "ontology_results": ontology_results,
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
        "scoringMethod": "ontology_signals token match (openai client + virtuoso)",
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
