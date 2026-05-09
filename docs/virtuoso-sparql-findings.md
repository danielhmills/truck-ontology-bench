# Virtuoso SPARQL Migration — Findings

## 1. Current Architecture (Microsoft Fabric GQL)

The project implements a **5-stage pipeline** that is tightly coupled to Microsoft Fabric:

```
ontology.md ──► ParsedOntology ──► Fabric Config ──► Fabric Ontology API
                                                         │
                                            ┌────────────┤
                                            ▼            ▼
                                     Lakehouse Tables   Graph Model
                                     (Livy/Spark SQL)   (auto-provisioned)
                                            │            │
                                            └─────┬──────┘
                                                  ▼
                                           GQL executeQuery
                                                  │
                                                  ▼
                                           Query Results
```

### Key integration points (all Fabric-specific)

| Layer | Module | What It Does |
|-------|--------|--------------|
| Auth | `fabric_client/auth.py` | OAuth2 client_credentials → Entra AD |
| Config | `fabric_client/config.py` | 5 env vars: tenant, client, secret, workspace, lakehouse |
| Schema push | `fabric_client/definition_builder.py` | Builds Fabric entity types, relationship types, data bindings, contextualizations |
| Table create | `fabric_client/lakehouse_sync.py` | CREATE TABLE + INSERT via Livy Spark SQL |
| Livy session | `fabric_client/livy_api.py` | Spark session management for SQL execution |
| Graph refresh | `fabric_client/graph_api.py` | POST refresh job, LRO poll |
| Graph query | `fabric_client/graph_api.py` | POST executeQuery with GQL |
| Ontology CRUD | `fabric_client/ontology_api.py` | Create/update/delete Fabric ontology items |
| Agent provisioning | `agents/provision.py` | Fabric Data Agent REST API (NakedAgent + OntologyAgent) |
| LRO polling | `fabric_client/lro.py` | Shared long-running-operation poller |

### What stays the same (domain-neutral)

| Layer | Module | Reason |
|-------|--------|--------|
| Ontology source | `input/schema/ontology.md` | Single source of truth; Markdown format is tool-agnostic |
| Markdown parser | `markdown_parser/` | Produces neutral `ParsedOntology` dataclass |
| Seed data | `input/data/seed/*.jsonl` | 11 files, ~960 rows; format is independent of backend |
| Scenarios | `scenarios/truck_scenarios.json` | 18 benchmark questions + gold answers |
| Scoring | `scoring/evaluator.py` | 7-dimension scoring is answer-format agnostic |
| Script 01 | `scripts/01_parse_md.py` | Parse Markdown → JSON (no Fabric dependency) |
| Script 06 | `scripts/06_score.py` | Score agent results (reads JSON, not Fabric) |

## 2. What Must Change

### 2.1 Ontology Representation: Fabric Definition → RDF/OWL

**Current**: `definition_builder.py` produces Fabric-specific JSON parts:
- Entity types with Fabric `$schema` URLs, namespace metadata, property definitions
- Relationship types sourcing/targeting entity type IDs
- Lakehouse data bindings (column→property mappings)
- Contextualizations (FK column mappings for relationships)

**Target**: An **OWL ontology** in Turtle format with:
- `owl:Class` for each entity (Terminal, Truck, Driver, ...)
- `owl:DatatypeProperty` for each field (name, city, employee_id, ...)
- `owl:ObjectProperty` for each relationship (truck_home_terminal, trip_driver, ...)
- Domain and range assertions linking properties to classes

**Design decisions needed**:
- Base URI (e.g., `http://example.org/truck-ontology#`)
- Instance URI pattern (e.g., `/data/{entity}/{pk}`)
- Whether to use `owl:imports` or inline everything

### 2.2 Data Loading: Livy/Lakehouse → RDF Bulk Load

**Current**: `lakehouse_sync.py` + `livy_api.py`:
1. Open Livy Spark session
2. `DROP TABLE IF EXISTS trk_*`
3. `CREATE TABLE trk_* (...)`
4. Parse JSONL → `INSERT INTO trk_* VALUES (...)`
5. Fabric auto-materializes Delta tables → graph nodes/edges

**Target**: New `rdf_builder/data_converter.py`:
1. Map each JSONL row to RDF triples
2. Generate instance URIs from entity type + primary key
3. Map FK columns to object property triples
4. Output N-Triples or Turtle file
5. Bulk load into Virtuoso (via `ld_dir` + `rdf_loader_run()` or SPARQL INSERT)

**Key mapping**:
```
JSONL row: {"terminal_id": "T001", "name": "Atlanta Hub", "city": "Atlanta", ...}
     ↓
RDF triples:
  :data/terminal/T001  rdf:type        :Terminal .
  :data/terminal/T001  :terminal_id    "T001" .
  :data/terminal/T001  :name           "Atlanta Hub" .
  :data/terminal/T001  :city           "Atlanta" .

JSONL FK: {"truck_id": "TRK001", "home_terminal_id": "T001", ...}
     ↓
Additional triple:
  :data/truck/TRK001   :truck_home_terminal  :data/terminal/T001 .
```

### 2.3 Query Language: GQL → SPARQL

All 11 competency queries must be rewritten. Here is the mapping for each:

| Query | GQL Pattern | SPARQL Equivalent | Fabric Status |
|-------|-------------|-------------------|---------------|
| cq01 | `MATCH (n) RETURN COUNT(n)` | `SELECT (COUNT(DISTINCT ?n) AS ?c) WHERE { ?n a ?type }` | Passes |
| cq02 | Edge + GROUP BY + ORDER BY | Property path + `GROUP BY` + `ORDER BY` | Passes |
| cq03 | Edge + DISTINCT + LIMIT | `SELECT DISTINCT` + `LIMIT` | Passes |
| cq04 | 3 parallel edges from one node | 3 triple patterns sharing `?tr` | Passes |
| cq05 | Edge + GROUP BY on target attr | Same pattern | Passes |
| cq06 | Edge + COUNT + GROUP BY | Same pattern | Passes |
| cq07 | Edge + SUM + GROUP BY | Same pattern | Passes |
| **cq08** | `LIKE '%H%'` | `FILTER(CONTAINS(?x, "H"))` | **FAILS on Fabric** |
| cq09 | Property filter + DISTINCT | `FILTER(?sev = "critical")` | Passes |
| **cq10** | `CASE WHEN ... END` inside `SUM()` | `SUM(IF(?cond, 1, 0))` or sub-SELECT | **FAILS on Fabric** |
| **cq11** | `WHERE NOT EXISTS { ... }` | `FILTER NOT EXISTS { ... }` | **FAILS on Fabric** |

All three queries that fail on the Fabric GQL engine (cq08, cq10, cq11) are expressible in standard SPARQL 1.1 and should work on Virtuoso.

### 2.4 Graph Refresh: Eliminated

Virtuoso does not need a separate "refresh" step. Once RDF triples are loaded, they are immediately queryable via SPARQL. The `--skip-refresh` flag and LRO polling infrastructure (`lro.py`) become unnecessary.

### 2.5 Agent Infrastructure: Removed

The Fabric Data Agent concept (NakedAgent + OntologyAgent) has no direct equivalent in the Virtuoso ecosystem. The benchmark comparison (raw SQL vs. semantic graph) can be:
- **Option A**: Removed — the Virtuoso benchmark focuses purely on SPARQL query correctness
- **Option B**: Replaced — compare SPARQL queries against the ontology vs. SQL queries against the relational tables
- **Option C**: Externalized — keep the benchmark scenarios but run them via an LLM that can issue SPARQL

### 2.6 Configuration

| Current (.env) | Target (.env) |
|----------------|---------------|
| `AZURE_TENANT_ID` | `VIRTUOSO_HOST` (default: localhost) |
| `AZURE_CLIENT_ID` | `VIRTUOSO_PORT` (default: 8890) |
| `AZURE_CLIENT_SECRET` | `VIRTUOSO_SPARQL_ENDPOINT` (default: /sparql) |
| `FABRIC_WORKSPACE_ID` | `VIRTUOSO_USER` (default: dba) |
| `FABRIC_LAKEHOUSE_ID` | `VIRTUOSO_PASSWORD` (default: dba) |
| — | `VIRTUOSO_GRAPH_URI` (named graph, optional) |

### 2.7 Script Changes

| Script | Current | Change |
|--------|---------|--------|
| `01_parse_md.py` | Parses Markdown → parsed_ontology.json | **No change** |
| `02_build_mapping.py` | Parsed → Fabric config | **Rewritten**: Parsed → RDF ontology (Turtle) + data mapping |
| `03_setup.py` | Fabric ontology + Livy tables + bindings | **Rewritten**: Load RDF ontology → Virtuoso, convert JSONL → RDF, bulk load |
| `04_refresh_and_validate.py` | Graph refresh + GQL queries | **Rewritten**: Run SPARQL queries directly (no refresh) |
| `05_setup_agents.py` | Fabric Data Agents | **Removed or replaced** |
| `06_score.py` | Score agent results | **Adapted** for new output format |

## 3. Module Map: What Gets Replaced

```
fabric_client/auth.py              → virtuoso_client/auth.py         (basic auth, simpler)
fabric_client/config.py            → virtuoso_client/config.py       (different env vars)
fabric_client/graph_api.py         → virtuoso_client/sparql_api.py   (SPARQL protocol)
fabric_client/ontology_api.py      → (removed)
fabric_client/definition_builder.py → rdf_builder/ontology_builder.py (OWL generation)
fabric_client/lakehouse_sync.py    → rdf_builder/data_converter.py   (JSONL → RDF)
fabric_client/livy_api.py          → (removed)
fabric_client/lro.py               → (removed)
fabric_client/data_agent_api.py    → (removed)
agents/provision.py                → (removed or replaced)
agents/instructions.py             → agents/instructions.py          (GQL → SPARQL)
gql-queries/*.gql                  → sparql-queries/*.rq             (11 files)
```

## 4. Risks and Considerations

1. **URI Design**: Must decide on a stable base URI and instance URI pattern before generating RDF
2. **Named Graphs**: Whether to load all triples into a single named graph or use the default graph
3. **Virtuoso Bulk Load**: `ld_dir` + `rdf_loader_run()` is fast but requires filesystem access to the Virtuoso server. SPARQL INSERT works over HTTP but is slower for ~960 rows × many triples
4. **Inference**: Virtuoso can do RDFS/OWL inference. Whether to enable it affects query results (e.g., subclass reasoning)
5. **Data Types**: Need to map Markdown types to XSD types (string→xsd:string, int→xsd:integer, float→xsd:double, boolean→xsd:boolean, datetime→xsd:dateTime, date→xsd:date)
6. **Agent Benchmark Parity**: Without Fabric Data Agents, the NakedAgent vs. OntologyAgent comparison needs rethinking
