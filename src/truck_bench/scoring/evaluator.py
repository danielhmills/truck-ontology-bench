"""Scorer for agent responses.

The primary signal is the critic's Yes/No/Unclear verdict when available
(packed into ``response.reasoning`` via a ``__critic_verdict__=`` marker).
When the verdict is absent, the scorer falls back to a deterministic
token-match on ``ontology_signals`` with separator normalization, so
``write_off_flag`` matches ``write-off flag``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .scenarios import GoldenAnswer, Scenario


@dataclass
class AgentResponse:
    scenario_id: str
    agent_type: str  # "naked" | "ontology"
    answer: str = ""
    reasoning: str = ""
    sql_or_gql: str = ""
    metric_selected: str = ""
    tables_used: list[str] = field(default_factory=list)
    relationships_used: list[str] = field(default_factory=list)
    ambiguity_flagged: bool = False
    action_policy: str = "execute"
    error: str | None = None


@dataclass
class ScoreResult:
    scenario_id: str
    agent_type: str
    metric_correct: bool = False
    tables_correct: bool = False
    relationships_correct: bool = False
    ambiguity_detected: bool = False
    guardrail_respected: bool = False
    signals_correct: bool = False
    numeric_correct: bool = False
    total_score: int = 0
    max_score: int = 0
    notes: str = ""


_NUMBER_PATTERN = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")


def _extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for m in _NUMBER_PATTERN.finditer(text or ""):
        token = m.group(0).replace(",", "").replace("$", "")
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def score_numeric(answer: str, gold_value: float, tolerance_pct: float) -> bool:
    """Return True if any number in ``answer`` is within ``tolerance_pct``% of ``gold_value``."""
    numbers = _extract_numbers(answer)
    if not numbers:
        return False
    if gold_value == 0:
        threshold = abs(tolerance_pct)
        return any(abs(n) <= threshold for n in numbers)
    threshold = abs(gold_value) * (tolerance_pct / 100.0)
    return any(abs(n - gold_value) <= threshold for n in numbers)


def normalize_text(s: str) -> str:
    """Fold camelCase, underscores, hyphens, slashes, whitespace to a single space."""
    s = (s or "")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = s.lower()
    s = re.sub(r"[_\-/]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def score_signals(answer: str, signals: list[str]) -> tuple[bool, list[str], list[str]]:
    if not signals:
        return True, [], []
    norm_answer = normalize_text(answer)
    matched: list[str] = []
    missing: list[str] = []
    for s in signals:
        if normalize_text(s) in norm_answer:
            matched.append(s)
        else:
            missing.append(s)
    return len(missing) == 0, matched, missing


def _extract_critic_verdict(response: AgentResponse) -> str | None:
    text = response.reasoning or ""
    marker = "__critic_verdict__="
    if marker not in text:
        return None
    line = text.split(marker, 1)[1].splitlines()[0].strip().lower()
    return line if line in {"yes", "no", "unclear"} else None


def score_response(response: AgentResponse, golden: GoldenAnswer) -> ScoreResult:
    """Score one ``AgentResponse`` against its ``GoldenAnswer``.

    The critic verdict (when available) is ONE independent dimension,
    not a full override. Every dimension the golden declares an
    expectation for is scored and contributes 1 point to ``max_score``
    and ``total_score``.
    """
    result = ScoreResult(scenario_id=response.scenario_id, agent_type=response.agent_type)
    notes: list[str] = []

    if response.error:
        notes.append(f"error: {response.error}")

    # 0. Critic verdict as one of N dimensions
    verdict = _extract_critic_verdict(response)
    if verdict is not None:
        result.max_score += 1
        if verdict == "yes":
            result.total_score += 1
            notes.append("critic: yes")
        elif verdict == "unclear":
            notes.append("critic: unclear")
        else:
            notes.append("critic: no")

    # 1. Signal-token lexical coverage — independent dimension, always
    # scored when the golden answer declares signals. This is LEXICAL
    # COVERAGE only (did the agent mention the expected terms), not
    # numeric correctness.
    if golden.ontology_signals:
        ok, _matched, missing = score_signals(response.answer, golden.ontology_signals)
        result.max_score += 1
        if ok:
            result.signals_correct = True
            result.total_score += 1
        else:
            notes.append(f"signals missing: {missing}")

    # 2. Deterministic numeric gold — independent dimension. Passes if
    # the agent's free-text answer contains a number within tolerance of
    # the gold value. Guards against lexically-correct answers with
    # wrong numbers.
    if golden.gold_numeric_value is not None:
        result.max_score += 1
        if score_numeric(
            response.answer,
            golden.gold_numeric_value,
            golden.gold_numeric_tolerance_pct,
        ):
            result.numeric_correct = True
            result.total_score += 1
        else:
            notes.append(
                f"numeric: expected ~{golden.gold_numeric_value} "
                f"(±{golden.gold_numeric_tolerance_pct}%)"
            )

    result.notes = "; ".join(notes) if notes else "no dimensions scored"
    return result


def score_all(
    responses: list[AgentResponse],
    golden_answers: dict[str, GoldenAnswer],
) -> list[ScoreResult]:
    return [
        score_response(r, golden_answers[r.scenario_id])
        for r in responses
        if r.scenario_id in golden_answers
    ]


def generate_scorecard(
    naked_results: list[ScoreResult],
    ontology_results: list[ScoreResult],
    scenarios: list[Scenario] | None = None,
) -> str:
    naked_by = {r.scenario_id: r for r in naked_results}
    onto_by = {r.scenario_id: r for r in ontology_results}
    scen_by = {s.scenario_id: s for s in (scenarios or [])}
    ids = sorted(set(naked_by) | set(onto_by))

    lines = [
        "# NakedAgent vs OntologyAgent — scorecard",
        "",
        "| Scenario | Domain | Naked | Ontology | Winner | Notes |",
        "|----------|--------|-------|----------|--------|-------|",
    ]

    n_tot = n_max = o_tot = o_max = 0
    for sid in ids:
        n = naked_by.get(sid)
        o = onto_by.get(sid)
        dom = scen_by[sid].domain if sid in scen_by else ""
        n_str = f"{n.total_score}/{n.max_score}" if n else "-"
        o_str = f"{o.total_score}/{o.max_score}" if o else "-"
        if n and o:
            n_tot += n.total_score; n_max += n.max_score
            o_tot += o.total_score; o_max += o.max_score
            if n.total_score > o.total_score:
                winner = "Naked"
            elif o.total_score > n.total_score:
                winner = "Ontology"
            else:
                winner = "tie"
        else:
            winner = "-"
        note_pieces = []
        if n and n.notes and n.notes != "critic: yes":
            note_pieces.append(f"N: {n.notes}")
        if o and o.notes and o.notes != "critic: yes":
            note_pieces.append(f"O: {o.notes}")
        lines.append(f"| {sid} | {dom} | {n_str} | {o_str} | {winner} | {' | '.join(note_pieces)} |")

    def pct(num: int, denom: int) -> str:
        return f"{round(100 * num / denom)}%" if denom else "-"

    lines.extend([
        "",
        "## Summary",
        "",
        "| Agent | Score | Max | Accuracy |",
        "|-------|-------|-----|----------|",
        f"| Naked | {n_tot} | {n_max} | {pct(n_tot, n_max)} |",
        f"| Ontology | {o_tot} | {o_max} | {pct(o_tot, o_max)} |",
    ])
    return "\n".join(lines)
