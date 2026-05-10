# gql-queries/

**Competency queries — the ontology's unit tests.** Each `cq*.gql`
file asks one small question you should be able to answer *after a
clean setup + graph refresh*. If any of them fails, the ontology or
its bindings are wrong.

`scripts/04_refresh_and_validate.py` picks up every file matching
`cq*.gql`, sorted by name, runs it against the graph model, and
records pass/fail in `outputs/_validation.json`.

## The 11 queries at a glance

| File | Pattern exercised |
|---|---|
| `cq01.gql` | Total node count (sanity after a refresh) |
| `cq02.gql` | Single-entity aggregation + `GROUP BY` (`Truck → Terminal` via `truck_home_terminal`) |
| `cq03.gql` | Edge traversal with `DISTINCT` (`DriverHOSLog → Driver`) |
| `cq04.gql` | Three parallel edges out of one entity (`Trip → Driver / Truck / Route`) |
| `cq05.gql` | Two-entity join with `GROUP BY` on target property (`Load → Customer`) |
| `cq06.gql` | Aggregate across an edge (`Trip → Route` trip counts) |
| `cq07.gql` | Numeric aggregation over property (`MaintenanceEvent → Truck` cost by `make`) |
| `cq08.gql` | Semantic intersection via `LIKE` on array-as-string (hazmat endorsement on `Load` ∩ `Driver`) |
| `cq09.gql` | Property filter on edge target (`ServiceTicket → Truck` where severity='critical') |
| `cq10.gql` | Conditional aggregation numerator / denominator (on-time delivery rate) |
| `cq11.gql` | Anti-join / `NOT EXISTS` (trucks that never had a `MaintenanceEvent`) |

See each file's header comment for the exact question it asks.

## Running them

```bash
# After 03_setup.py has created the ontology + loaded tables:
python scripts/04_refresh_and_validate.py
```

Expected output ends with:

```
Results: 11 passed, 0 failed out of 11  -> outputs/_validation.json
```

The Fabric UI also works — open the graph model and paste any one of
these files into its query editor. The row counts should match.

## File format + how to add one

The header is `//`-style comments; the body is plain GQL passed to
the graph model's `executeQuery` endpoint. Format spec + "how to add
a new competency query" recipe:
[`../docs/04-schema-reference.md#gql-query-file-format`](../docs/04-schema-reference.md#gql-query-file-format).

## Where GQL is still weak (noted, not hidden)

`cq10` and `cq11` exercise two patterns that Fabric's current agent
authoring handles poorly even though the queries themselves run fine
here:

* `cq10` uses `CASE WHEN … THEN … ELSE 0 END` inside `SUM()`. The
  agent often refuses to author this pattern.
* `cq11` uses `WHERE NOT EXISTS { ... }`. The agent often refuses to
  express anti-joins in GQL.

Both are documented in
[`../docs/05-troubleshooting.md`](../docs/05-troubleshooting.md) and
in the honest pros/cons section of the top-level README. The queries
live here as demonstrations that the graph model supports them — the
gap is in the agent's expressiveness, not the engine's.
