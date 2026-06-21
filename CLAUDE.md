# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env              # defaults: local extractor + local generator (qwen3:8b via Ollama)
ollama pull qwen3:8b              # required for default extractor + generator
uvicorn api:app --reload          # Gradio UI at / · FastAPI at /chat, /reset, /graph/{session_id}
pytest                            # all tests; no external services needed (uses stub + echo)
pytest tests/test_orchestrator.py::test_turn_fills_graph_and_shrinks_missing  # single test
```

Offline (no Ollama): set `EXTRACTOR=stub GENERATOR=echo` in `.env` before running.
Backend selection: `GRAPH_BACKEND` (memory|neo4j), `EXTRACTOR` (stub|local), `GENERATOR` (echo|local|openrouter).

## Architecture

CACTUS CBT therapy chatbot: a local LLM (`qwen3:8b` via Ollama) plays therapist,
following CACTUS paper principles (guided discovery, questioner not advisor). A
knowledge graph tracks what has been revealed in the session. A Cytoscape.js panel
in the Gradio UI shows the live graph state.

The point of the codebase is the **swap architecture** — a real clinical ontology
drops into `schema.py` and real prompts into `prompts.py` without touching anything
else. See `docs/PRD_cactus_v5.md` for full product spec and `DESIGN.md` (local,
gitignored) for the per-file implementation guide.

### The dependency rule (load-bearing)

`interfaces.py` defines four `Protocol`s — `Schema`, `GraphStore`, `Extractor`,
`Generator` — plus `OntologyField`. **Every module except `factory.py` may import
only from `interfaces.py`**, never from the concretes. `factory.py` is the single
place that knows which implementation backs each protocol; env vars select at
construction time.

### The turn loop

`orchestrator.turn(session, user_message)`:

1. `extractor.extract(message, schema_text)` → `{field_key: value}` deltas
2. `graph.apply_deltas(deltas, turn_id)` → persists acquired clinical fields
3. `graph.cbt_context()` → structured string (phase, technique, acquired/missing)
4. Format `CBT_SYSTEM_PROMPT` with context + last-10-turn history
5. `generator.generate(system, history)` → `{"response", "technique", "phase"}`
6. `validate_phase()` — enforces minimum-turns/fields before accepting phase advance
7. `graph.apply_session_state(phase, technique)` → persists session-state fields
8. Return `{reply, technique, phase, deltas, slots}`

The graph drives turn-taking: `missing()` lists unacquired clinical fields in
priority order (excludes priority-0 session-state fields). The LLM decides phrasing;
`validate_phase()` in `orchestrator.py` enforces session structure.

### Schema fields

`CBTSchema` has 9 clinical fields (priority 1–9, filled by `LocalLLMExtractor`
from client speech) and 2 session-state fields (priority 0, written by the LLM's
JSON output): `session_phase` and `active_technique`. `missing()` never surfaces
priority-0 fields.

### Implementation notes worth knowing

- `LocalLLMGenerator` uses Ollama's **native `/api/chat`**, not `/v1`. Always
  passes `"think": false`. The `LOCAL_LLM_BASE_URL` env var accepts `/v1` for
  convenience but the suffix is stripped internally.
- `LocalLLMExtractor` uses `/api/generate` with `format: "json"`. Unknown keys
  returned by the model are silently dropped. JSON parse failures return `{}`.
- `Generator.generate()` returns `dict` (`{"response", "technique", "phase"}`),
  not `str`. `EchoGenerator` returns the same shape for offline testing.
- The Cytoscape.js graph panel in `ui.py` polls `GET /graph/{session_id}` every
  3 seconds. The endpoint returns Cytoscape-compatible node/edge JSON derived from
  `graph.snapshot()`.
- Sessions are process-local dicts in `api.py`. Restart loses chat history.
- `api.py` mounts Gradio **after** FastAPI routes are defined.
