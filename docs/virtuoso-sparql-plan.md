# Virtuoso SPARQL Migration — Implementation Plan

## Summary

Replace the Microsoft Fabric GQL backend with a Virtuoso SPARQL endpoint. The Markdown ontology source, seed data, scenarios, and scoring remain unchanged. All Fabric-specific infrastructure (Livy, Lakehouse, Ontology API, Graph API, Data Agents) is replaced with RDF/OWL generation, Virtuoso bulk loading, and SPARQL query execution.

---

## Phase 1: RDF Ontology Builder (replaces `definition_builder.py`)

### 1.1 Create `src/truck_bench/rdf_builder/` package

New module structure:
```
src/truck_bench/rdf_builder/
├── __init__.py
├── ontology_builder.py    # ParsedOntology → OWL Turtle
└── data_converter.py      # JSONL seed files → RDF Turtle/N-Triples
```

### 1.2 `ontology_builder.py` — Generate OWL ontology from ParsedOntology

**Input**: `ParsedOntology` (from `markdown_parser`)

**Output**: OWL ontology in Turtle format

**Logic**:
1. Define base URI: `http://example.org/truck-ontology#`
2. Define data URI prefix: `http://example.org/truck-ontology/data/`
3. For each `ParsedEntity`, emit:
   - `owl:Class` declaration
   - `owl:DatatypeProperty` for each field (domain → entity class, range → xsd type)
   - Mark the PK field(s) as functional properties or add an `owl:hasKey` axiom
4. For each FK relationship (from `ParsedOntology.foreign_keys()`), emit:
   - `owl:ObjectProperty` with domain=source class, range=target class
   - Name follows existing convention: `{snake(source)}_{fk_column_without_id}`

**XSD type mapping**:
```python
MARKDOWN_TO_XSD = {
    "String": "xsd:string",
    "BigInt": "xsd:integer",
    "Int": "xsd:integer",
    "Double": "xsd:double",
    "Boolean": "xsd:boolean",
    "DateTime": "xsd:dateTime",
    "Date": "xsd:date",
}
```

**Turtle output example**:
```turtle
@prefix : <http://example.org/truck-ontology#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

:Terminal  rdf:type  owl:Class .
:truck_home_terminal  rdf:type  owl:ObjectProperty ;
    rdfs:domain  :Truck ;
    rdfs:range   :Terminal .
:name  rdf:type  owl:DatatypeProperty ;
    rdfs:domain  :Terminal ;
    rdfs:range   xsd:string .
```

### 1.3 `data_converter.py` — Convert JSONL seed data to RDF

**Input**: Seed JSONL files + ontology config (entity→property→type mapping)

**Output**: Single N-Triples file (`outputs/truck_data.nt`)

**Logic**:
1. For each entity config, read its JSONL file
2. For each row, generate an instance URI: `<http://example.org/truck-ontology/data/{snake_entity}/{pk_value}>`
3. Emit `rdf:type` triple
4. Emit datatype property triples for each non-FK column (with XSD-typed literals)
5. Emit object property triples for each FK column (reference target instance URI)
6. Skip null/missing FK values

**Special handling**:
- `datetime`/`date` fields: emit with `^^xsd:dateTime` or `^^xsd:date`
- `uuid` fields: emit as plain string literals
- Array fields (`string[]`, `int[]`): emit as string literals (same as Fabric behavior)
- Null FK: skip the object property triple

**N-Triples output example**:
```nt
<http://example.org/truck-ontology/data/terminal/T001> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.org/truck-ontology#Terminal> .
<http://example.org/truck-ontology/data/terminal/T001> <http://example.org/truck-ontology#terminal_id> "T001" .
<http://example.org/truck-ontology/data/terminal/T001> <http://example.org/truck-ontology#name> "Atlanta Hub" .
<http://example.org/truck-ontology/data/terminal/T001> <http://example.org/truck-ontology#city> "Atlanta" .
```

---

## Phase 2: Virtuoso Client (replaces `fabric_client/`)

### 2.1 Create `src/truck_bench/virtuoso_client/` package

```
src/truck_bench/virtuoso_client/
├── __init__.py
├── config.py       # VirtuosoConfig dataclass
└── sparql_api.py   # SPARQL query + bulk load
```

### 2.2 `config.py` — Environment configuration

```python
@dataclass(frozen=True)
class VirtuosoConfig:
    host: str              # VIRTUOSO_HOST (default: localhost)
    port: int              # VIRTUOSO_PORT (default: 8890)
    user: str              # VIRTUOSO_USER (default: dba)
    password: str          # VIRTUOSO_PASSWORD (default: dba)
    sparql_endpoint: str   # VIRTUOSO_SPARQL_ENDPOINT (default: /sparql)
    graph_uri: str | None  # VIRTUOSO_GRAPH_URI (optional named graph)
```

**`.env.example` update**:
```
VIRTUOSO_HOST=localhost
VIRTUOSO_PORT=8890
VIRTUOSO_USER=dba
VIRTUOSO_PASSWORD=dba
VIRTUOSO_SPARQL_ENDPOINT=/sparql
VIRTUOSO_GRAPH_URI=http://example.org/truck-ontology/graph
```

### 2.3 `sparql_api.py` — SPARQL protocol client

```python
class SparqlClient:
    def __init__(self, config: VirtuosoConfig): ...

    def execute_query(self, query: str, 
                      format: str = "application/json") -> dict:
        """POST to /sparql with query, return JSON results."""
        # POST {host}:{port}{sparql_endpoint}
        # Content-Type: application/x-www-form-urlencoded
        # Accept: application/sparql-results+json
        # Body: query={encoded_query}

    def load_file(self, file_path: Path, graph_uri: str | None = None):
        """Load an RDF file via Virtuoso's /sparql-auth endpoint or ld_dir."""
        # Option A: ld_dir + rdf_loader_run() via SQL
        # Option B: SPARQL INSERT via LOAD or inline triples

    def clear_graph(self, graph_uri: str | None = None):
        """CLEAR GRAPH <uri> or CLEAR DEFAULT."""

    def check_loaded(self) -> int:
        """SELECT COUNT(*) WHERE { ?s ?p ?o } — verify data is loaded."""
```

**SPARQL endpoint details**:
- URL: `http://{host}:{port}{sparql_endpoint}`
- Method: POST with `application/x-www-form-urlencoded`
- Auth: HTTP Basic (or Digest)
- Response format: `application/sparql-results+json`

---

## Phase 3: SPARQL Queries (replaces `gql-queries/*.gql`)

### 3.1 Create `sparql-queries/` directory with 11 `.rq` files

| File | Core SPARQL Pattern |
|------|---------------------|
| `cq01.rq` | `SELECT (COUNT(DISTINCT ?n) AS ?total_nodes) WHERE { ?n a ?type . FILTER(?type IN (...)) }` |
| `cq02.rq` | Edge traversal + `GROUP BY` + `ORDER BY DESC()` |
| `cq03.rq` | `SELECT DISTINCT` across edge + `LIMIT 20` |
| `cq04.rq` | Three triple patterns on same `?tr` subject, each following a different object property |
| `cq05.rq` | Edge + `GROUP BY` on target entity property |
| `cq06.rq` | Edge + `COUNT` + `GROUP BY` + `ORDER BY DESC()` |
| `cq07.rq` | Edge + `SUM` + `GROUP BY` + `ORDER BY DESC()` |
| `cq08.rq` | Cartesian between Load and Driver + `FILTER(CONTAINS(...))` on both endorsement fields |
| `cq09.rq` | Edge + `FILTER(?severity = "critical")` + `SELECT DISTINCT` |
| `cq10.rq` | Sub-SELECT: one for total count, one for on-time count (filtered by `?arr <= ?dwe`) |
| `cq11.rq` | `FILTER NOT EXISTS { ?me a :MaintenanceEvent ; :maintenance_event_truck ?t }` |

**SPARQL translation rules** (from GQL patterns):

| GQL | SPARQL |
|-----|--------|
| `MATCH (a:Label)` | `?a rdf:type :Label` |
| `MATCH (a)-[:rel]->(b:Label)` | `?a :rel ?b . ?b rdf:type :Label` |
| `RETURN a.prop AS name` | `?a :prop ?name` in triple pattern |
| `WHERE a.prop = 'val'` | `FILTER(?prop = "val")` |
| `WHERE a.prop LIKE '%X%'` | `FILTER(CONTAINS(?prop, "X"))` |
| `CASE WHEN cond THEN 1 ELSE 0 END` | `IF(cond, 1, 0)` |
| `WHERE NOT EXISTS { ... }` | `FILTER NOT EXISTS { ... }` |
| `COUNT(n)` | `COUNT(?n)` or `COUNT(DISTINCT ?n)` |
| `SUM(expr)` | `SUM(?var)` |
| `DISTINCT a.prop` | `SELECT DISTINCT ?prop` |
| `ORDER BY col DESC` | `ORDER BY DESC(?col)` |
| `LIMIT 25` | `LIMIT 25` |
| `GROUP BY alias` | `GROUP BY ?var` |

**cq01 SPARQL example** (full):
```sparql
PREFIX : <http://example.org/truck-ontology#>

SELECT (COUNT(DISTINCT ?node) AS ?total_nodes)
WHERE {
  ?node a ?type .
  FILTER(?type IN (
    :Terminal, :Truck, :Trailer, :Driver, :Customer,
    :Route, :Load, :Trip, :MaintenanceEvent, :ServiceTicket, :DriverHOSLog
  ))
}
```

**cq10 SPARQL example** (full — replaces CASE WHEN):
```sparql
PREFIX : <http://example.org/truck-ontology#>

SELECT ?total_delivered ?on_time_count
WHERE {
  { SELECT (COUNT(*) AS ?total_delivered) WHERE { ?tr a :Trip ; :trip_load ?l . ?l a :Load } }
  { SELECT (COUNT(*) AS ?on_time_count) WHERE {
      ?tr a :Trip ; :trip_load ?l ; :actual_arrival ?arr .
      ?l :delivery_window_end ?dwe .
      FILTER(?arr <= ?dwe)
  } }
}
```

**cq11 SPARQL example** (full — replaces NOT EXISTS):
```sparql
PREFIX : <http://example.org/truck-ontology#>

SELECT ?truck_number ?make ?model
WHERE {
  ?t a :Truck ;
     :truck_number ?truck_number ;
     :make ?make ;
     :model ?model .
  FILTER NOT EXISTS {
    ?me a :MaintenanceEvent ;
        :maintenance_event_truck ?t .
  }
}
LIMIT 25
```

---

## Phase 4: Script Rewrites

### 4.1 `scripts/01_parse_md.py` — No change

This script is backend-neutral. It reads `ontology.md` and writes `outputs/parsed_ontology.json`.

### 4.2 `scripts/02_build_rdf.py` — New (replaces `02_build_mapping.py`)

```
Input:  outputs/parsed_ontology.json
Output: outputs/truck_ontology.ttl       (OWL ontology)
        outputs/ontology-config.json     (metadata for data loading step)
```

Uses `rdf_builder/ontology_builder.py` to generate the OWL ontology Turtle file.

The `ontology-config.json` retains entity→table mapping metadata needed by the data converter (entity names, property names, types, key info, FK references — but not Fabric-specific fields like workspace_id).

### 4.3 `scripts/03_load_virtuoso.py` — New (replaces `03_setup.py`)

```
Input:  outputs/ontology-config.json
        input/data/seed/*.jsonl
        outputs/truck_ontology.ttl
Output: outputs/_state.json             (virtuoso host, graph URI, entity count)
```

Steps:
1. Load `VirtuosoConfig` from `.env`
2. Convert JSONL → N-Triples using `rdf_builder/data_converter.py`
3. Clear existing named graph (if any): `CLEAR GRAPH <...>`
4. Load OWL ontology via SPARQL INSERT (or file upload)
5. Load N-Triples data via SPARQL INSERT (or bulk `ld_dir`)
6. Verify: `SELECT COUNT(*) WHERE { ?s ?p ?o }` matches expected triple count
7. Write `_state.json` with graph URI and load metadata

**Loading strategy**: For ~960 rows × ~10 fields × ~3 triples/field ≈ ~30K triples, SPARQL INSERT over HTTP is fast enough. If performance becomes an issue, switch to Virtuoso's `ld_dir` + `rdf_loader_run()` bulk loader.

### 4.4 `scripts/04_validate_sparql.py` — Modified (replaces `04_refresh_and_validate.py`)

```
Input:  outputs/_state.json
        sparql-queries/*.rq
Output: outputs/_validation.json
```

Steps:
1. Read `_state.json` for graph URI
2. For each `.rq` file in `sparql-queries/`:
   - Read query (strip `#` comment headers)
   - POST to Virtuoso `/sparql`
   - Parse SPARQL JSON results
   - Record pass/fail + row count
3. Write `_validation.json`

**Key difference from Fabric version**: No graph refresh step. SPARQL queries run directly against loaded triples. The `--skip-refresh` flag is removed. The retry logic for Fabric's auto-cancelled refresh jobs is gone.

### 4.5 `scripts/05_run_benchmark.py` — Optional (replaces `05_setup_agents.py`)

If the agent comparison is preserved, this becomes a script that:
1. Loads 18 scenarios
2. Sends each scenario's natural-language question to an LLM
3. LLM generates SPARQL (OntologyAgent) or SQL (NakedAgent)
4. Executes the generated query against Virtuoso
5. Records results for scoring

If agents are removed, this script is deleted and `06_score.py` reads results directly from `04_validate_sparql.py` output.

### 4.6 `scripts/06_score.py` — Adapted input format

Same scoring logic, but reads from the new validation output format.

---

## Phase 5: Agent Instructions Update

### 5.1 `agents/instructions.py` — SPARQL references

Update `ONTOLOGY_AGENT_INSTRUCTIONS`:
- Replace "GQL (Graph Query Language)" → "SPARQL"
- Replace "the ontology runtime resolves queries" → "queries are executed against the Virtuoso SPARQL endpoint"
- Replace "GQL aggregation" section → "SPARQL aggregation" with equivalent guidance
- Keep all domain terminology (FMCSA HOS, J1939, CDL endorsements)

If agents are removed, skip this phase.

---

## Phase 6: Cleanup

### 6.1 Delete Fabric-specific code

These files are removed entirely:
```
src/truck_bench/fabric_client/auth.py
src/truck_bench/fabric_client/ontology_api.py
src/truck_bench/fabric_client/graph_api.py
src/truck_bench/fabric_client/definition_builder.py
src/truck_bench/fabric_client/lakehouse_sync.py
src/truck_bench/fabric_client/livy_api.py
src/truck_bench/fabric_client/lro.py
src/truck_bench/fabric_client/data_agent_api.py
```

`fabric_client/config.py` either removed or kept behind a flag for backward compatibility.

### 6.2 Update dependencies

`pyproject.toml` changes:
- Remove: none (only `requests`, `python-dotenv`, `pytest` are dependencies — all still needed)
- Add: none (SPARQL uses `requests` + `application/x-www-form-urlencoded`)

### 6.3 Update tests

- Remove tests that mock Fabric APIs (`test_fabric_*.py`)
- Add tests for:
  - OWL ontology generation
  - JSONL → RDF conversion
  - SPARQL query translation

---

## Phase 7: Documentation

### 7.1 Update existing docs

| Doc | Change |
|-----|--------|
| `docs/01-fabric-iq-primer.md` | Replace with Virtuoso SPARQL primer |
| `docs/02-repo-tour.md` | Update code map for new module structure |
| `docs/03-walkthrough.md` | Rewrite first-run playbook for Virtuoso |
| `docs/04-schema-reference.md` | Add OWL/RDF + SPARQL formats |
| `docs/05-troubleshooting.md` | Replace Fabric errors with Virtuoso errors |
| `README.md` | Update architecture diagram, quickstart |

### 7.2 New docs

| Doc | Content |
|-----|---------|
| `docs/virtuoso-setup.md` | Installing/running Virtuoso, creating the SPARQL endpoint |
| `docs/sparql-reference.md` | SPARQL patterns used, mapping from GQL examples |

---

## Migration Sequence (Recommended Order)

```
1. rdf_builder/ontology_builder.py     ── OWL generation (test with parsed ontology)
2. rdf_builder/data_converter.py       ── JSONL → RDF conversion (test with small seed)
3. virtuoso_client/                    ── config + SPARQL client
4. sparql-queries/                     ── 11 .rq files (can test independently)
5. scripts/02_build_rdf.py             ── parse → OWL, end-to-end
6. scripts/03_load_virtuoso.py         ── convert + load, end-to-end
7. scripts/04_validate_sparql.py       ── SPARQL execution, end-to-end
8. agents/instructions.py              ── GQL → SPARQL (if keeping agents)
9. scripts/05, scripts/06              ── benchmark + scoring (if keeping agents)
10. Cleanup + docs                     ── remove Fabric modules, update docs
```

## Estimated Scope

| Phase | New files | Modified files | Deleted files |
|-------|-----------|----------------|---------------|
| 1. RDF Builder | 3 | 0 | 0 |
| 2. Virtuoso Client | 2 | 1 (.env.example) | 0 |
| 3. SPARQL Queries | 11 | 0 | 0 |
| 4. Scripts | 2 (scripts 02, 03) | 2 (scripts 04, 06) | 1 (script 05, optional) |
| 5. Agent Instructions | 0 | 1 | 0 |
| 6. Cleanup | 0 | 2 (pyproject, __init__) | 7 (Fabric modules) |
| 7. Documentation | 2 | 5 | 0 |
| **Total** | **20** | **11** | **8** |
