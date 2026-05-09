"""06 - Score the NakedAgent vs OntologyAgent comparison.

Inputs:
    outputs/_agent_comparison.json    (produced by the Fabric notebook)
    scenarios/truck_scenarios.json    (optional override)
Outputs:
    outputs/scorecard.md
    outputs/scorecard.json

Scenario locking
----------------
The comparison JSON produced by the notebook is the source of truth for
which questions were asked. That file MUST include
``scenariosSha256`` + ``scenariosPayload``; the scorer reads the
embedded payload by default. Pass ``--scenarios-from local`` to override
with a local file, and either match the embedded sha256 or pass
``--override-scenario-hash`` to accept the divergence deliberately.

perQuestion rows are joined to scenarios by ``scenario_id``. Missing or
duplicate IDs fail hard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from truck_bench.scoring import (  # noqa: E402
    AgentResponse,
    generate_scorecard,
    golden_answers_from_scenarios,
    score_all,
)
from truck_bench.scoring.scenarios import Scenario  # noqa: E402


_POSITIVE = {"yes", "true", "1"}
_NEGATIVE = {"no", "false", "0"}


def _verdict_of(row: dict, suffix: str) -> str:
    for col in (f"evaluation_judgement_{suffix}",
                f"evaluation_result_{suffix}",
                f"evaluation_status_{suffix}"):
        raw = row.get(col)
        if raw is None:
            continue
        if isinstance(raw, bool):
            return "yes" if raw else "no"
        s = str(raw).strip().lower()
        if not s:
            continue
        if s in _POSITIVE:
            return "yes"
        if s in _NEGATIVE:
            return "no"
        if s == "unclear":
            return "unclear"
    return ""


def _build_response(sid: str, agent_type: str, row: dict) -> AgentResponse:
    ans = row.get(f"actual_answer_{agent_type}", "") or ""
    resp = AgentResponse(
        scenario_id=sid,
        agent_type=agent_type,
        answer=ans,
        reasoning=ans,
        sql_or_gql=ans,
        error=None,
    )
    resp.reasoning = f"__critic_verdict__={_verdict_of(row, agent_type)}\n{ans}"
    return resp


def _scenarios_from_payload(payload: list[dict]) -> list[Scenario]:
    return [
        Scenario(**{k: v for k, v in s.items() if k in Scenario.__dataclass_fields__})
        for s in payload
    ]


def _canonical_sha256(payload: list[dict]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _index_rows_by_scenario_id(per_question: list[dict]) -> dict[str, dict]:
    by_sid: dict[str, dict] = {}
    missing_idx: list[int] = []
    duplicates: list[str] = []
    for i, row in enumerate(per_question):
        sid = row.get("scenario_id")
        if not sid:
            missing_idx.append(i)
            continue
        if sid in by_sid:
            duplicates.append(sid)
            continue
        by_sid[sid] = row
    if missing_idx:
        raise RuntimeError(
            f"perQuestion rows missing scenario_id at indices {missing_idx}."
        )
    if duplicates:
        raise RuntimeError(f"duplicate scenario_id(s) in perQuestion: {duplicates}")
    return by_sid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--comparison", default=REPO_ROOT / "outputs" / "_agent_comparison.json", type=Path)
    parser.add_argument("--scenarios", default=REPO_ROOT / "scenarios" / "truck_scenarios.json", type=Path,
                        help="Local scenarios file; consulted only when "
                             "--scenarios-from local is passed.")
    parser.add_argument("--scenarios-from", choices=("comparison", "local"),
                        default="comparison",
                        help="Which scenarios payload to score against. "
                             "'comparison' (default) uses the embedded "
                             "scenariosPayload. 'local' uses --scenarios and "
                             "verifies its sha against scenariosSha256.")
    parser.add_argument("--override-scenario-hash", action="store_true",
                        help="Allow --scenarios-from local to proceed even "
                             "when the local file's sha256 does not match "
                             "scenariosSha256 in the comparison JSON.")
    parser.add_argument("--md-out", default=REPO_ROOT / "outputs" / "scorecard.md", type=Path)
    parser.add_argument("--json-out", default=REPO_ROOT / "outputs" / "scorecard.json", type=Path)
    args = parser.parse_args()

    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
    per_question = comparison.get("perQuestion", [])
    embedded_payload = comparison.get("scenariosPayload")
    embedded_sha = comparison.get("scenariosSha256")

    hash_override_note: str | None = None

    if args.scenarios_from == "comparison":
        if not embedded_payload:
            raise RuntimeError(
                f"{args.comparison} has no scenariosPayload — this file was "
                f"produced by a pre-R04 notebook. Rerun with --scenarios-from "
                f"local --override-scenario-hash to score anyway."
            )
        scenarios_payload = embedded_payload
        scenarios_sha256 = embedded_sha or _canonical_sha256(embedded_payload)
        source_description = f"embedded in {args.comparison}"
    else:
        if not args.scenarios.exists():
            raise RuntimeError(f"--scenarios file not found: {args.scenarios}")
        scenarios_bytes = args.scenarios.read_bytes()
        scenarios_payload = json.loads(scenarios_bytes.decode("utf-8"))
        local_sha = hashlib.sha256(scenarios_bytes).hexdigest()
        local_canonical_sha = _canonical_sha256(scenarios_payload)
        matches = embedded_sha in (None, local_sha, local_canonical_sha)
        if not matches:
            if not args.override_scenario_hash:
                raise RuntimeError(
                    f"Local scenarios sha256 ({local_sha}) does not match "
                    f"scenariosSha256 in the comparison JSON ({embedded_sha}). "
                    f"Pass --override-scenario-hash if intentional."
                )
            hash_override_note = (
                f"local sha {local_sha} != embedded sha {embedded_sha}; "
                f"--override-scenario-hash accepted"
            )
            print(f"WARNING: {hash_override_note}")
        scenarios_sha256 = local_sha
        source_description = str(args.scenarios)

    scenarios = _scenarios_from_payload(scenarios_payload)
    if not scenarios:
        raise RuntimeError("resolved scenarios list is empty")
    by_sid = {s.scenario_id: s for s in scenarios}
    if len(by_sid) != len(scenarios):
        seen: dict[str, int] = {}
        for s in scenarios:
            seen[s.scenario_id] = seen.get(s.scenario_id, 0) + 1
        dups = [sid for sid, n in seen.items() if n > 1]
        raise RuntimeError(f"duplicate scenario_id(s) in scenarios payload: {dups}")
    golden_answers = golden_answers_from_scenarios(scenarios)

    rows_by_sid = _index_rows_by_scenario_id(per_question)

    unknown_ids = sorted(set(rows_by_sid) - set(by_sid))
    if unknown_ids:
        raise RuntimeError(
            f"perQuestion contains scenario_id(s) not in the scenarios "
            f"payload: {unknown_ids}"
        )

    naked_responses: list[AgentResponse] = []
    ontology_responses: list[AgentResponse] = []

    for sid, scenario in by_sid.items():
        row = rows_by_sid.get(sid)
        if row is None:
            print(f"  (no agent answer for {sid} in comparison JSON — skipping)")
            continue
        naked_responses.append(_build_response(sid, "naked", row))
        ontology_responses.append(_build_response(sid, "ontology", row))

    naked_scores = score_all(naked_responses, golden_answers)
    onto_scores = score_all(ontology_responses, golden_answers)

    md = generate_scorecard(naked_scores, onto_scores, scenarios)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(md, encoding="utf-8")

    def _dump(results):
        return [
            {k: getattr(r, k) for k in ("scenario_id", "agent_type", "metric_correct",
                                          "tables_correct", "relationships_correct",
                                          "ambiguity_detected", "guardrail_respected",
                                          "signals_correct", "numeric_correct",
                                          "total_score", "max_score", "notes")}
            for r in results
        ]

    output = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenariosSource": source_description,
        "scenariosSha256": scenarios_sha256,
        "scenariosPayload": scenarios_payload,
        "naked": _dump(naked_scores),
        "ontology": _dump(onto_scores),
    }
    if hash_override_note:
        output["scenarioHashOverride"] = hash_override_note
    args.json_out.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(md)
    print()
    print(f"Scenarios:  {source_description}  (sha256 {scenarios_sha256[:12]}...)")
    print(f"Wrote {args.md_out}")
    print(f"Wrote {args.json_out}")


if __name__ == "__main__":
    main()
