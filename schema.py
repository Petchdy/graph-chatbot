"""CBT intake schema. Drop a real clinical ontology in here without touching anything else."""

from interfaces import OntologyField

_CLINICAL_FIELDS = [
    OntologyField(key="presenting_problem", description="The main issue or concern the client describes.", priority=1),
    OntologyField(key="emotion",            description="The client's current or described emotional state.", priority=2),
    OntologyField(key="negative_thought",   description="A specific negative or irrational thought the client expresses.", priority=3),
    OntologyField(key="cognitive_pattern",  description="The cognitive distortion pattern (e.g. catastrophizing, labeling, overgeneralization).", priority=4),
    OntologyField(key="trigger_situation",  description="The external situation or event that triggered the distress.", priority=5),
    OntologyField(key="physical_symptoms",  description="Any physical symptoms mentioned (tension, sleep issues, fatigue, etc.).", priority=6),
    OntologyField(key="past_coping",        description="What the client has tried before to cope with the problem.", priority=7),
    OntologyField(key="reframe_attempt",    description="Any reframing or alternative perspective the client generates themselves.", priority=8),
    OntologyField(key="coping_strategies",  description="Strategies discussed or agreed upon during the session.", priority=9),
]

_SESSION_STATE_FIELDS = [
    OntologyField(key="session_phase",    description="Current session phase: Rapport / Exploration / Technique / Consolidation.", priority=0),
    OntologyField(key="active_technique", description="The CBT technique currently being applied.", priority=0),
]


class CBTSchema:
    """9 clinical fields (extracted from client speech) + 2 session-state fields
    (set by LLM JSON output, never surfaced as 'missing' to ask about)."""

    def fields(self) -> list[OntologyField]:
        return list(_SESSION_STATE_FIELDS + _CLINICAL_FIELDS)

    def render(self) -> str:
        """Renders only clinical fields — session-state fields are not for extraction."""
        return "\n".join(f"- {f.key}: {f.description}" for f in _CLINICAL_FIELDS)
