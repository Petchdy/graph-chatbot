"""The turn loop. Depends only on interfaces.py — never on concrete implementations."""

from dataclasses import dataclass, field

from interfaces import Extractor, GraphStore, Generator, Schema
from prompts import CBT_SYSTEM_PROMPT

PHASE_ORDER = ["Rapport", "Exploration", "Technique", "Consolidation"]

PHASE_MINIMUMS: dict[str, dict] = {
    "Exploration":   {"fields": ["presenting_problem"],                     "min_turns": 2},
    "Technique":     {"fields": ["presenting_problem", "negative_thought"],  "min_turns": 5},
    "Consolidation": {"fields": ["negative_thought", "cognitive_pattern"],   "min_turns": 12},
}


def validate_phase(proposed: str, current: str, snapshot: dict, turn_count: int) -> str:
    """Accept proposed phase only if the minimum field/turn requirements are met."""
    try:
        if PHASE_ORDER.index(proposed) <= PHASE_ORDER.index(current):
            return proposed
    except ValueError:
        return current
    mins = PHASE_MINIMUMS.get(proposed, {})
    fields_met = all(snapshot.get(f, {}).get("acquired") for f in mins.get("fields", []))
    turns_met = turn_count >= mins.get("min_turns", 0)
    return proposed if (fields_met and turns_met) else current


@dataclass
class Session:
    schema: Schema
    graph: GraphStore
    extractor: Extractor
    generator: Generator
    history: list[tuple[str, str]] = field(default_factory=list)
    turn_count: int = 0


def turn(session: Session, user_message: str) -> dict:
    """Run one turn: extract → apply_deltas → build context → generate → validate phase → persist state."""
    session.turn_count += 1
    schema_text = session.schema.render()

    deltas = session.extractor.extract(user_message, schema_text)
    session.graph.apply_deltas(deltas, session.turn_count)

    cbt_context = session.graph.cbt_context()
    snapshot = session.graph.snapshot()
    current_phase = (snapshot.get("session_phase") or {}).get("value") or "Rapport"

    completed = [(u, a) for u, a in session.history if a]
    history_summary = "\n".join(
        f"Client: {u}\nTherapist: {a}" for u, a in completed[-10:]
    ) or "(session just started)"

    system_prompt = CBT_SYSTEM_PROMPT.format(
        cbt_context=cbt_context,
        history_summary=history_summary,
    )

    session.history.append((user_message, ""))
    result = session.generator.generate(system_prompt, session.history)

    reply = result.get("response", "")
    technique = result.get("technique", "Rapport Building")
    proposed_phase = result.get("phase", "Rapport")

    session.history[-1] = (user_message, reply)

    validated_phase = validate_phase(proposed_phase, current_phase, snapshot, session.turn_count)
    session.graph.apply_session_state(validated_phase, technique)

    return {
        "reply": reply,
        "technique": technique,
        "phase": validated_phase,
        "deltas": deltas,
        "slots": session.graph.snapshot(),
    }
