# Virtuoso + LangChain Agent Implementation Plan

## Context

The truck-ontology-bench project currently uses Microsoft Fabric IQ (Data Agents) to run a NakedAgent (SQL vs. Lakehouse) and OntologyAgent (GQL vs. Ontology Graph). We've already built the RDF generation layer (OWL ontology + Turtle data from JSONL). Now we need to:

1. Load the generated RDF into a Virtuoso SPARQL endpoint
2. Replace Fabric Data Agents with a LangChain-based agent architecture
3. Run the same 18 benchmark scenarios and produce comparable scores

The output is a self-contained benchmark that runs against `localhost:8890/sparql` with no Azure/Fabric dependencies.

## Phases

Each phase is a self-contained, testable increment with its own commit.

---

### Phase 0: Dependencies and Configuration

**Files to modify:**
- `pyproject.toml` — add `langchain`, `langchain-openai`, `openai`, `rdflib`
- `.env.example` — replace Fabric vars with Virtuoso + OpenAI vars

**New `.env.example`:**
```
# Virtuoso
VIRTUOSO_HOST=localhost
VIRTUOSO_PORT=8890
VIRTUOSO_USER=dba
VIRTUOSO_PASSWORD=dba
VIRTUOSO_SPARQL_ENDPOINT=/sparql
VIRTUOSO_GRAPH_URI=http://demo.openlinksw.com/trucking-ontology-benchmark/graph

# OpenAI
OPENAI_API_KEY=<your-key>
OPENAI_MODEL_NAME=gpt-4o-mini
```

**Commit:** `phase-00: deps and .env for virtuoso + langchain`

---

### Phase 1: Virtuoso Client

**Files to create:**
- `src/truck_bench/virtuoso_client/__init__.py`
- `src/truck_bench/virtuoso_client/config.py` — `VirtuosoConfig` dataclass (host, port, user, password, endpoint, graph_uri)
- `src/truck_bench/virtuoso_client/sparql_api.py` — `SparqlClient` with:
  - `execute_query(query)` → raw JSON
  - `execute_select(query)` → list of binding dicts
  - `load_ttl(ttl_text, graph_uri)` → SPARQL INSERT DATA
  - `load_file(path, graph_uri)` → read + load
  - `clear_graph(graph_uri)` → CLEAR GRAPH
  - `count_triples(graph_uri)` → SELECT COUNT

**Commit:** `phase-01: virtuoso_client with SparqlClient`

---

### Phase 2: SQLite Client for NakedAgent

**Rationale:** The NakedAgent needs a relational backend. SQLite is zero-dependency, queryable locally, and mirrors the original Lakehouse table layout.

**Files to create:**
- `src/truck_bench/sqlite_client/__init__.py`
- `src/truck_bench/sqlite_client/schema.py` — `TABLE_DEFINITIONS` dict with CREATE TABLE statements for all 11 entities (auto-derived from ontology.md fields, with snake_case column names matching JSONL keys)
- `src/truck_bench/sqlite_client/loader.py` — `load_jsonl_to_sqlite(db_path, seed_dir)` function that:
  1. Creates all 11 tables via `schema.py`
  2. Reads each JSONL file
  3. Inserts rows (converting Python lists to JSON strings for array columns)
  4. Returns row counts per table

**Commit:** `phase-02: sqlite_client with schema and JSONL loader`

---

### Phase 3: Load RDF into Virtuoso

**Files to create:**
- `scripts/04_load_virtuoso.py` — loads both TTL files into Virtuoso

**Workflow:**
1. Parse Markdown (for summary stats)
2. Read `outputs/truck_ontology.ttl`
3. Read `outputs/truck_data.ttl` (or just merge them)
4. Connect to Virtuoso via `SparqlClient`
5. Clear existing graph
6. Load ontology TTL (treat as SPARQL INSERT DATA into named graph — or better, use `requests` to POST raw Turtle to Virtuoso's `/sparql-graph-crud` endpoint if available, else fall back to splitting into manageable INSERT DATA chunks)
7. Load data TTL
8. Verify with `count_triples()`
9. Write `outputs/_state.json` with graph URI and triple count

**Alternative loading approach:** POST raw Turtle via HTTP PUT to Virtuoso's graph store endpoint (`/sparql-graph-crud?graph-uri=...`). This is much faster than SPARQL INSERT DATA for bulk loads.

**Commit:** `phase-03: script to load TTLs into virtuoso`

---

### Phase 4: SPARQL Competency Queries and Validation

**Files to create:**
- `sparql-queries/cq01.rq` through `cq11.rq` — 11 SPARQL translations of `gql-queries/*.gql`
- `scripts/05_validate_sparql.py` — executes all 11 queries against Virtuoso, records pass/fail

**Key translations (notably, what fails on Fabric GQL works in SPARQL):**

| cq | GQL pattern | SPARQL |
|----|-------------|--------|
| cq08 | `WHERE x LIKE '%H%'` | `FILTER(CONTAINS(?x, "H"))` |
| cq10 | `CASE WHEN ... END` | sub-SELECT with separate COUNTs |
| cq11 | `WHERE NOT EXISTS` | `FILTER NOT EXISTS { ... }` |

**Commit:** `phase-04: 11 SPARQL queries and validation script`

---

### Phase 5: Update Agent Instructions (GQL → SPARQL)

**File to modify:**
- `src/truck_bench/agents/instructions.py`

**Changes:**
1. `ONTOLOGY_AGENT_INSTRUCTIONS`: Replace all GQL references with SPARQL
   - "query with GQL" → "query with SPARQL"
   - "ontology runtime resolves queries" → "queries execute against the Virtuoso SPARQL endpoint"
   - Entity labels → SPARQL `rdf:type` patterns
   - GQL aggregation note → SPARQL aggregation patterns (`GROUP BY`, `COUNT`, `SUM`)
   - Add PREFIX declarations the agent should use

2. `NAKED_AGENT_INSTRUCTIONS`: Update table references for SQLite context
   - "Lakehouse tables" → "SQLite tables"
   - Add table schema summary

3. `ONTOLOGY_DS_INSTRUCTIONS`: Update relationship guidance for SPARQL property names (camelCase, FK `_id` stripped)

**Commit:** `phase-05: update agent instructions from GQL to SPARQL`

---

### Phase 6: LangChain Agent Implementation

**Files to create:**
- `src/truck_bench/agents/runner.py` — the core benchmark runner

**Architecture:**

Two LangChain tools are registered:
```python
@tool
def query_sparql(query: str) -> str:
    """Run SPARQL against the truck ontology graph at localhost:8890/sparql"""

@tool  
def query_sql(query: str) -> str:
    """Run SQL against the trucking SQLite database"""
```

Two agents are created from the same LLM, same benchmark loop, different tools:
- **OntologyAgent**: `create_tool_calling_agent(llm, [query_sparql], ONTOLOGY_AGENT_INSTRUCTIONS)`
- **NakedAgent**: `create_tool_calling_agent(llm, [query_sql], NAKED_AGENT_INSTRUCTIONS)`

Both agents share a `run_benchmark(scenarios, agent, tool, max_turns=5)` function that:
1. For each scenario, invokes the agent with the natural-language question
2. Parses the response (extracts SPARQL/SQL query, final answer)
3. Token-matches `ontology_signals` against the answer
4. Records results in the `_agent_comparison.json` format

**LLM config:**
```python
ChatOpenAI(
    model=os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
    temperature=0,
)
```

**Commit:** `phase-06: langchain agent runner with SPARQL and SQL tools`

---

### Phase 7: Benchmark Script

**Files to create:**
- `scripts/06_run_benchmark.py`

**Workflow:**
1. Load `VirtuosoConfig` and `SparqlClient`
2. Load/create SQLite database from JSONL seed
3. Load 18 scenarios from `scenarios/truck_scenarios.json`
4. Initialize LLM via `ChatOpenAI`
5. Create OntologyAgent (SPARQL tool) and NakedAgent (SQL tool)
6. For each scenario, query both agents
7. Score answers via token-match on `ontology_signals`
8. Write `outputs/_agent_comparison.json` in the format `06_score.py` expects
9. Print summary: correct counts per agent, accuracy %

**Commit:** `phase-07: benchmark script with langchain agents`

---

### Phase 8: Scoring Integration

**Files to create:**
- `scripts/07_score.py` — thin wrapper adapting `scripts/06_score.py` to read the new `_agent_comparison.json`

**Actually:** The existing `scripts/06_score.py` reads `_agent_comparison.json` and it already expects the exact format we produce. So this phase is just:
- Rename `scripts/06_score.py` → `scripts/07_score.py` (so 06 is benchmark, 07 is score)
- Verify the scoring runs end-to-end

**Commit:** `phase-08: wire scoring to benchmark output`

---

## Verification Plan

After each phase:
1. **Phase 0**: `pip install -e ".[dev]"` succeeds
2. **Phase 1**: `SparqlClient().execute_select("SELECT 1 AS ?x")` returns `[{"x": "1"}]`
3. **Phase 2**: `load_jsonl_to_sqlite("test.db", "input/data/seed")` creates 11 tables with correct row counts
4. **Phase 3**: `python scripts/04_load_virtuoso.py` loads all triples, count matches
5. **Phase 4**: `python scripts/05_validate_sparql.py` — all 11 queries pass
6. **Phase 5**: Read `instructions.py`, verify no GQL/Fabric references remain
7. **Phase 6**: Unit test: single scenario, agent returns structured answer
8. **Phase 7**: `python scripts/06_run_benchmark.py` — runs 18 scenarios, writes comparison JSON
9. **Phase 8**: `python scripts/07_score.py` — produces `scorecard.md` and `scorecard.json`

## Files Summary

| Action | Count |
|--------|-------|
| Create | ~20 files |
| Modify | 4 files (`pyproject.toml`, `.env.example`, `instructions.py`, `__init__.py`) |
| Delete | 0 (Fabric modules kept for reference, not imported) |
