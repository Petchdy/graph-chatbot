# PRD: CACTUS CBT Therapy Chatbot — v5 (Graph-Backed)

**Version:** 5.0  
**Status:** Ready for implementation  
**Target stack:** Python · FastAPI · Gradio · Ollama (qwen3:8b) · Neo4j (optional) / in-memory

---

## 1. What We're Building

A local CBT therapy chatbot where:
- **The user is the client**, the **LLM (qwen3:8b via Ollama) is the therapist**
- The therapist follows the CACTUS paper's principles: questioner-first, guided discovery, phase-structured CBT
- Every conversation turn **extracts clinical facts** from what the client says and writes them into a **knowledge graph**
- The graph drives session awareness: the therapist knows what's been revealed, what phase the session is in, and which CBT technique is active
- A **Gradio side panel** shows the live knowledge graph (Cytoscape.js, polling) alongside the chat

This is a direct evolution of `V4_flat`. The swap architecture (`interfaces.py`, `factory.py`, env-var selection) is **preserved unchanged**. Only the content layer changes: new schema, new prompts, new graph fields, new Gradio panel.

---

## 2. Decisions Already Made

| Decision | Choice | Notes |
|---|---|---|
| Schema approach | Option A (flat fields) | Richer typed graph (Option B) deferred |
| Phase transitions | Hybrid | Deterministic minimums enforced by graph; LLM decides when to advance within those bounds |
| Graph as controller | Yes | `missing()` becomes a soft hint; graph also tracks phase + technique as state |
| LLM | qwen3:8b via Ollama native `/api/chat` | `"think": false` required; do NOT use `/v1` endpoint — thinking tokens cause stalls |
| JSON reliability | Use as-is | Retry/fallback wrapper noted as future work if Qwen reliability is poor |
| Graph visualization | Cytoscape.js in Gradio side panel, polling `/graph/{session_id}` | Path 1: stays inside Gradio/FastAPI process |
| Neo4j vs in-memory | Both supported, in-memory default | Neo4j opt-in via `GRAPH_BACKEND=neo4j` |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI + Gradio (port 8000)             │
│                                                             │
│  POST /chat/{session_id}   ←── client message               │
│  POST /reset/{session_id}  ←── reset session                │
│  GET  /graph/{session_id}  ←── Cytoscape node/edge JSON     │
│                                                             │
│  Gradio UI (mounted at /)                                   │
│  ┌──────────────────┬──────────────────────────────────┐    │
│  │   Chat panel     │   Graph panel (Cytoscape.js)     │    │
│  │                  │   polls /graph every 3s          │    │
│  │  [client types]  │   nodes: Session, Field, Turn    │    │
│  │  [therapist rpl] │   colored by type, labeled       │    │
│  └──────────────────┴──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    orchestrator.turn()                      │
│                                                             │
│  1. extractor.extract(message)   → clinical deltas dict     │
│  2. graph.apply_deltas(deltas)   → persist to graph         │
│  3. graph.cbt_context()          → structured context str   │
│  4. BUILD system prompt          → CBT therapist prompt     │
│  5. generator.generate()         → JSON {response,          │
│                                          technique, phase}  │
│  6. graph.apply_session_state()  → update phase+technique   │
│  7. return reply + graph snapshot                           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────┐    ┌──────────────────────────────────────┐
│  GraphStore  │    │  Ollama qwen3:8b (localhost:11434)   │
│  (memory or  │    │  - /api/generate  (extractor)        │
│   Neo4j)     │    │  - /api/chat      (generator)        │
└──────────────┘    └──────────────────────────────────────┘
```

---

## 4. The Dependency Rule (Do Not Break)

Inherited from V4_flat. **Every module except `factory.py` imports only from `interfaces.py`**. Never import concrete classes (`InMemoryGraphStore`, `LocalLLMExtractor`, etc.) outside `factory.py`.

---

## 5. File-by-File Changes

### 5.1 `interfaces.py` — Add two new methods to `GraphStore`

Add these two methods to the `GraphStore` Protocol (both implementations must satisfy them):

```python
def cbt_context(self) -> str:
    """Return a human-readable summary of the current clinical knowledge
    and session state for injection into the therapist system prompt.
    Example output:
      Phase: Exploration (turn 4)
      Active technique: Evidence-Based Questioning
      Acquired: presenting_problem="work stress", emotion="anxious"
      Missing (soft): cognitive_pattern, reframe_attempt, coping_strategies
    """
    ...

def apply_session_state(self, phase: str, technique: str) -> None:
    """Update the session-level state nodes (phase, active technique)
    returned by the LLM in its JSON response."""
    ...
```

Everything else in `interfaces.py` stays the same.

---

### 5.2 `schema.py` — Replace `PlaceboSchema` with `CBTSchema`

Delete `PlaceboSchema`. Add `CBTSchema`.

**Fields** (these are the flat ontology fields the extractor fills):

| key | description | priority |
|---|---|---|
| `presenting_problem` | The main issue or concern the client describes | 1 |
| `emotion` | The client's current or described emotional state | 2 |
| `negative_thought` | A specific negative or irrational thought the client expresses | 3 |
| `cognitive_pattern` | The cognitive distortion pattern (e.g., catastrophizing, labeling) | 4 |
| `trigger_situation` | The external situation or event that triggered the distress | 5 |
| `physical_symptoms` | Any physical symptoms mentioned (tension, sleep issues, etc.) | 6 |
| `past_coping` | What the client has tried before to cope | 7 |
| `reframe_attempt` | Any reframing or alternative perspective the client generates themselves | 8 |
| `coping_strategies` | Strategies discussed or agreed upon during the session | 9 |

**Session-state fields** (not extracted from client text; updated by LLM JSON output):

| key | description | priority |
|---|---|---|
| `session_phase` | Current phase: Rapport / Exploration / Technique / Consolidation | 0 |
| `active_technique` | The CBT technique currently being applied | 0 |

Session-state fields have priority 0 so they are never surfaced as "missing" fields to ask the client about. The graph stores them but `missing()` excludes priority-0 fields.

---

### 5.3 `graph.py` — Add `cbt_context()` and `apply_session_state()`

#### `InMemoryGraphStore`

```python
def cbt_context(self) -> str:
    phase = self._state.get("session_phase", {}).get("value") or "Rapport"
    technique = self._state.get("active_technique", {}).get("value") or "none yet"
    acquired_lines = [
        f'  {key}="{entry["value"]}"'
        for key, entry in self._state.items()
        if entry["acquired"] and key not in ("session_phase", "active_technique")
    ]
    missing_keys = self.missing()  # already excludes priority-0
    return (
        f"Session phase: {phase}\n"
        f"Active CBT technique: {technique}\n"
        f"What we know so far:\n" + ("\n".join(acquired_lines) or "  (nothing yet)") + "\n"
        f"Still to explore (soft hints, not a checklist): {', '.join(missing_keys) or 'none'}"
    )

def apply_session_state(self, phase: str, technique: str) -> None:
    self._state["session_phase"]["value"] = phase
    self._state["session_phase"]["acquired"] = True
    self._state["active_technique"]["value"] = technique
    self._state["active_technique"]["acquired"] = True
```

#### `Neo4jGraphStore`

Same logic, implemented with Cypher. Store `session_phase` and `active_technique` as `Field` nodes with `priority = 0`. Cypher for `apply_session_state`:

```cypher
MATCH (s:Session {id: $session_id})-[:HAS_FIELD]->(f:Field {key: $key})
SET f.value = $value, f.acquired = true
```

Run twice: once for `session_phase`, once for `active_technique`.

`missing()` Cypher must filter `WHERE field.priority > 0` to exclude session-state fields.

---

### 5.4 `prompts.py` — Replace placebo templates with CBT prompts

#### `CBT_SYSTEM_PROMPT` (used by generator)

```
You are a compassionate CBT therapist conducting a real counseling session.
You follow the CACTUS research principles:

CORE RULES:
- You are a QUESTIONER, not an answer-provider. Guide the client to discover
  their own insights. Never suggest reframes directly.
- Empathize only with what the client has actually expressed. Do not anticipate
  or project emotions beyond what they have shared.
- Ask exactly ONE question per turn. Never list multiple questions.
- Use plain, warm language. No clinical jargon.
- Do not name CBT techniques to the client.

SESSION PHASES AND PHASE TRANSITIONS:
  Rapport (turns 1-3): Build trust, understand why they came today.
    → Advance to Exploration when: presenting_problem is known.
  Exploration (turns 4-8): Identify the negative thought and what triggers it.
    → Advance to Technique when: negative_thought AND cognitive_pattern are known
      AND at least 5 turns have passed.
  Technique (turns 9+): Apply your chosen CBT technique through guided questions.
    → Advance to Consolidation when: at least one reframe_attempt has been captured
      AND at least 12 turns have passed.
  Consolidation: Help client articulate their own insight and next steps.

CBT TECHNIQUES AVAILABLE:
- Evidence-Based Questioning: guide client to find evidence for/against their thought
- Alternative Perspective: ask how someone else might view this situation
- Decatastrophizing: explore realistic likelihood of feared outcome
- Reality Testing: distinguish between the thought and actual experience
- Pros and Cons Analysis: explore advantages/disadvantages of the belief
- Efficiency Evaluation: evaluate whether the belief is actually useful
- Continuum Technique: position experience between two extremes
- Changing Rules to Wishes: replace rigid "must/should" with realistic hopes
- Problem-Solving Skills: systematic exploration of options
- Behavior Experiment: consider testing a belief with a small action

CURRENT SESSION CONTEXT:
{cbt_context}

CONVERSATION HISTORY:
{history_summary}

RESPONSE FORMAT — respond ONLY with valid JSON, no other text:
{{
  "response": "your warm, questioning therapist reply to the client",
  "technique": "name of technique being used, or 'Rapport Building' or 'Assessment'",
  "phase": "Rapport | Exploration | Technique | Consolidation"
}}
```

#### `CBT_EXTRACTION_PROMPT` (used by extractor)

```
You are a clinical information extractor. Your job is to read a client's message
in a CBT therapy session and extract any clinical facts that are clearly present.

FIELDS TO EXTRACT (only extract what is explicitly stated or very clearly implied):
{ontology_schema}

RULES:
- Only extract fields you are confident about from this message alone.
- Do NOT infer or guess. If unsure, omit the field.
- Return ONLY a JSON object with field keys and string values.
- Omit fields that are not present in this message.
- Do not extract session_phase or active_technique (those are set by the therapist).

Client message:
{message}

Respond ONLY with a JSON object. Example: {{"emotion": "anxious", "trigger_situation": "a work presentation"}}
```

Remove all old placebo templates. Keep the variable names `CBT_SYSTEM_PROMPT` and `CBT_EXTRACTION_PROMPT` (update import references in orchestrator and extract).

---

### 5.5 `extract.py` — Update `LocalLLMExtractor`

Change the prompt to use `CBT_EXTRACTION_PROMPT` from `prompts.py`. The mechanics (Ollama `/api/generate`, `format: "json"`, filter returned keys against schema) stay the same.

**One change**: add a fallback — if the returned JSON contains keys not in the schema, silently drop them (already done). If JSON parse fails, return `{}` and log a warning rather than raising.

`StubExtractor` stays unchanged (used for tests).

---

### 5.6 `generate.py` — Update `LocalLLMGenerator` for JSON output

The generator now expects the LLM to return JSON. Changes:

1. System prompt injected via `CBT_SYSTEM_PROMPT` (passed in from orchestrator as `system` arg, already the case).
2. After `response = requests.post(...)`, parse the returned text as JSON:
   ```python
   import json, re
   raw = result_text.strip()
   # strip any accidental markdown fences
   raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
   parsed = json.loads(raw)
   # return the full dict, not just parsed["response"]
   return parsed  # {"response": str, "technique": str, "phase": str}
   ```
3. **Change return type**: `generate()` now returns `dict` instead of `str`. Update the `Generator` Protocol in `interfaces.py` accordingly:
   ```python
   def generate(self, system: str, history: list[tuple[str, str]]) -> dict:
       ...
   ```
4. `EchoGenerator` must return a dict stub:
   ```python
   return {"response": "Echo: ...", "technique": "Rapport Building", "phase": "Rapport"}
   ```
5. If JSON parse fails, return a fallback dict with `response` set to the raw text, `technique` = `"Rapport Building"`, `phase` = current known phase (or `"Rapport"`). Log a warning.

`OpenRouterGenerator` is not the active path for this project. Leave it, but update its return type to dict for consistency.

---

### 5.7 `orchestrator.py` — Updated turn loop

```python
def turn(session: Session, user_message: str) -> dict:
    session.turn_count += 1
    schema_text = session.schema.render()

    # Step 1-2: extract clinical facts and persist
    deltas = session.extractor.extract(user_message, schema_text)
    session.graph.apply_deltas(deltas, session.turn_count)

    # Step 3: get full CBT context from graph
    cbt_context = session.graph.cbt_context()

    # Step 4: build history summary (last 10 turns for context window)
    history_window = session.history[-10:]
    history_summary = "\n".join(
        f"Client: {u}\nTherapist: {a}" for u, a in history_window if a
    )

    # Step 5: build system prompt
    from prompts import CBT_SYSTEM_PROMPT
    system_prompt = CBT_SYSTEM_PROMPT.format(
        cbt_context=cbt_context,
        history_summary=history_summary or "(session just started)"
    )

    # Step 6: generate therapist response (returns dict)
    session.history.append((user_message, ""))
    result = session.generator.generate(system_prompt, session.history)

    reply = result.get("response", "")
    technique = result.get("technique", "Rapport Building")
    phase = result.get("phase", "Rapport")

    session.history[-1] = (user_message, reply)

    # Step 7: update session state in graph
    session.graph.apply_session_state(phase, technique)

    return {
        "reply": reply,
        "technique": technique,
        "phase": phase,
        "deltas": deltas,
        "slots": session.graph.snapshot(),
    }
```

---

### 5.8 `api.py` — Add `/graph/{session_id}` endpoint

Add one new endpoint. Keep `/chat` and `/reset` as-is (update response model to include `technique` and `phase`).

```python
@app.get("/graph/{session_id}")
def get_graph(session_id: str):
    """Return Cytoscape-compatible node/edge JSON for the session graph."""
    if session_id not in sessions:
        return {"nodes": [], "edges": []}
    
    session = sessions[session_id]
    snapshot = session.graph.snapshot()
    
    nodes = []
    edges = []
    
    # Session node
    nodes.append({
        "data": {"id": "session", "label": f"Session\n{session_id}", "type": "session"}
    })
    
    # Field nodes + edges from session
    for key, entry in snapshot.items():
        if entry["acquired"]:
            node_id = f"field_{key}"
            label = f"{key}\n{str(entry['value'])[:30]}"
            node_type = "session_state" if key in ("session_phase", "active_technique") else "field"
            nodes.append({
                "data": {"id": node_id, "label": label, "type": node_type, "acquired": True}
            })
            edges.append({
                "data": {"source": "session", "target": node_id, "label": "HAS"}
            })
        else:
            # Show missing fields as dimmed nodes
            nodes.append({
                "data": {
                    "id": f"field_{key}",
                    "label": key,
                    "type": "missing",
                    "acquired": False
                }
            })
            edges.append({
                "data": {"source": "session", "target": f"field_{key}", "label": "MISSING"}
            })
    
    return {"nodes": nodes, "edges": edges}
```

---

### 5.9 `ui.py` — Two-column Gradio layout with live graph panel

Replace the current Gradio UI with a two-column layout:

**Left column**: Chat interface (same as before, but displays `technique` and `phase` as info labels below each therapist message).

**Right column**: Graph panel — a Gradio `HTML` component that contains a full Cytoscape.js visualization. It polls `/graph/{session_id}` every 3 seconds and re-renders.

**Cytoscape node colors**:
- `session` node: dark green `#2d6a4f`
- `field` (acquired): sage green `#74c69d`
- `session_state` (phase/technique): amber `#f4a261`
- `missing` (not yet acquired): light grey `#dee2e6`, dashed border

**Cytoscape layout**: `cose` (force-directed, built-in to Cytoscape.js). Fits well for small graphs (< 15 nodes).

**HTML component template** (inject session_id at render time):

```html
<div id="cy" style="width:100%; height:500px; border:1px solid #dee2e6; border-radius:8px;"></div>
<div id="graph-status" style="font-size:11px; color:#6b7280; padding:4px 8px;">Waiting for data…</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>
<script>
const SESSION_ID = "{session_id}";
const POLL_INTERVAL = 3000;

const cy = cytoscape({
  container: document.getElementById('cy'),
  style: [
    { selector: 'node[type="session"]',       style: { 'background-color': '#2d6a4f', 'color': '#fff', 'label': 'data(label)', 'text-wrap': 'wrap', 'font-size': '11px', 'text-valign': 'center', 'width': 80, 'height': 80, 'shape': 'ellipse' }},
    { selector: 'node[type="field"]',          style: { 'background-color': '#74c69d', 'label': 'data(label)', 'text-wrap': 'wrap', 'font-size': '10px', 'text-valign': 'center', 'width': 70, 'height': 70 }},
    { selector: 'node[type="session_state"]',  style: { 'background-color': '#f4a261', 'label': 'data(label)', 'text-wrap': 'wrap', 'font-size': '10px', 'text-valign': 'center', 'width': 70, 'height': 70, 'shape': 'diamond' }},
    { selector: 'node[type="missing"]',        style: { 'background-color': '#dee2e6', 'label': 'data(label)', 'font-size': '9px', 'text-valign': 'center', 'width': 50, 'height': 50, 'border-style': 'dashed', 'border-color': '#adb5bd', 'border-width': 1.5 }},
    { selector: 'edge',                        style: { 'label': 'data(label)', 'font-size': '9px', 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'line-color': '#adb5bd', 'target-arrow-color': '#adb5bd', 'arrow-scale': 0.8 }},
  ],
  layout: { name: 'cose', animate: false },
  elements: []
});

async function fetchAndRender() {
  try {
    const res = await fetch(`/graph/${SESSION_ID}`);
    const data = await res.json();
    cy.elements().remove();
    cy.add([...data.nodes, ...data.edges]);
    cy.layout({ name: 'cose', animate: false, randomize: false }).run();
    cy.fit(undefined, 20);
    document.getElementById('graph-status').textContent =
      `Updated: ${new Date().toLocaleTimeString()} · ${data.nodes.length} nodes`;
  } catch(e) {
    document.getElementById('graph-status').textContent = 'Graph fetch failed: ' + e.message;
  }
}

fetchAndRender();
setInterval(fetchAndRender, POLL_INTERVAL);
</script>
```

**Gradio layout skeleton**:

```python
with gr.Blocks(title="CACTUS Therapy") as demo:
    session_id_state = gr.State("default")

    with gr.Row():
        # Left: chat
        with gr.Column(scale=3):
            gr.Markdown("## 🌿 CACTUS CBT Therapy")
            phase_display = gr.Textbox(label="Session phase", interactive=False)
            technique_display = gr.Textbox(label="Active technique", interactive=False)
            chatbot = gr.Chatbot(height=420)
            msg_input = gr.Textbox(placeholder="Share what's on your mind…", show_label=False)
            send_btn = gr.Button("Send", variant="primary")

        # Right: live graph
        with gr.Column(scale=2):
            gr.Markdown("## 📊 Knowledge Graph")
            graph_panel = gr.HTML(value=render_graph_html("default"))
```

The `render_graph_html(session_id)` function returns the HTML template above with `{session_id}` filled in. Call it once at startup with `"default"` and again on reset.

---

### 5.10 `factory.py` — Update defaults

```python
def make_schema() -> Schema:
    return CBTSchema()   # was PlaceboSchema

def make_extractor() -> Extractor:
    kind = os.environ.get("EXTRACTOR", "local")   # default: local (was "stub")
    ...

def make_generator() -> Generator:
    kind = os.environ.get("GENERATOR", "local")   # default: local (was "echo")
    model = os.environ.get("LOCAL_LLM_MODEL", "qwen3:8b")
    ...
```

---

### 5.11 `.env.example` — Updated defaults

```bash
# Graph backend
GRAPH_BACKEND=memory
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme

# Extractor: "stub" (tests only) or "local" (default)
EXTRACTOR=local
OLLAMA_MODEL=qwen3:8b
OLLAMA_HOST=http://localhost:11434

# Generator: "echo" (tests only) or "local" (default)
GENERATOR=local
LOCAL_LLM_MODEL=qwen3:8b
LOCAL_LLM_BASE_URL=http://localhost:11434
# NOTE: base URL must NOT include /v1 — LocalLLMGenerator uses native /api/chat
```

---

### 5.12 `requirements.txt` — Add nothing new

Cytoscape.js is loaded from CDN in the HTML. No new Python packages needed.

---

## 6. Test Updates

The existing tests use `StubExtractor` + `EchoGenerator` + `InMemoryGraphStore`. They only need these changes:

1. `EchoGenerator.generate()` must now return a dict (see §5.6). Fix the return value.
2. Add `cbt_context()` and `apply_session_state()` to `InMemoryGraphStore`. Tests that call `snapshot()` still work.
3. Replace any reference to `PlaceboSchema` with `CBTSchema`.
4. Add one new test: `test_session_state_updates` — after a turn where the generator returns `{"phase": "Exploration", "technique": "Evidence-Based Questioning", ...}`, assert that `graph.snapshot()["session_phase"]["value"] == "Exploration"`.

---

## 7. Phase Transition Logic (Hybrid)

The LLM proposes a phase in its JSON output. The orchestrator enforces minimums before accepting an upgrade:

```python
PHASE_ORDER = ["Rapport", "Exploration", "Technique", "Consolidation"]
PHASE_MINIMUMS = {
    "Exploration":    {"fields": ["presenting_problem"],                       "min_turns": 2},
    "Technique":      {"fields": ["presenting_problem", "negative_thought"],   "min_turns": 5},
    "Consolidation":  {"fields": ["negative_thought", "cognitive_pattern"],    "min_turns": 12},
}

def validate_phase(proposed: str, current: str, snapshot: dict, turn_count: int) -> str:
    """Accept the proposed phase only if minimums are met. Otherwise keep current."""
    if PHASE_ORDER.index(proposed) <= PHASE_ORDER.index(current):
        return proposed  # same phase or going back is always allowed
    mins = PHASE_MINIMUMS.get(proposed, {})
    required_fields = mins.get("fields", [])
    min_turns = mins.get("min_turns", 0)
    fields_met = all(snapshot.get(f, {}).get("acquired", False) for f in required_fields)
    turns_met = turn_count >= min_turns
    return proposed if (fields_met and turns_met) else current
```

Add this function to `orchestrator.py`. Call it between step 5 (generate) and step 7 (apply_session_state):

```python
validated_phase = validate_phase(phase, current_phase, session.graph.snapshot(), session.turn_count)
session.graph.apply_session_state(validated_phase, technique)
```

The `current_phase` is read from `session.graph.snapshot().get("session_phase", {}).get("value", "Rapport")`.

---

## 8. File Change Summary

| File | Action | Scope |
|---|---|---|
| `interfaces.py` | Edit | Add 2 methods to `GraphStore` Protocol, change `Generator` return type to `dict` |
| `schema.py` | Replace | Delete `PlaceboSchema`, add `CBTSchema` with 11 fields |
| `graph.py` | Edit | Add `cbt_context()` + `apply_session_state()` to both store classes |
| `prompts.py` | Replace | Delete placebo templates, add `CBT_SYSTEM_PROMPT` + `CBT_EXTRACTION_PROMPT` |
| `extract.py` | Edit | Update `LocalLLMExtractor` to use `CBT_EXTRACTION_PROMPT`; add JSON fallback |
| `generate.py` | Edit | Return `dict` from all generators; add JSON parse + fallback in `LocalLLMGenerator` |
| `orchestrator.py` | Edit | Updated turn loop with `cbt_context()`, dict result, `validate_phase()` |
| `api.py` | Edit | Add `GET /graph/{session_id}`; update `/chat` response model |
| `ui.py` | Replace | Two-column layout, Cytoscape HTML panel, phase/technique labels |
| `factory.py` | Edit | `CBTSchema`; default extractor/generator to `local`; model to `qwen3:8b` |
| `.env.example` | Edit | New defaults |
| `tests/` | Edit | Fix `EchoGenerator` return type, add `CBTSchema`, add one new test |
| `README.md` | Edit | Update to reflect CBT use case and graph panel |
| `CLAUDE.md` | Edit | Update architecture section and commands |

---

## 9. Things NOT Changing

- `interfaces.py` Protocol structure (only additions, no removals)
- The dependency rule: only `factory.py` imports concretes
- The 5-step turn loop concept (steps 1-5 stay; steps 6-7 are additions)
- `Neo4jGraphStore` graph model (`(:Session)-[:HAS_FIELD]->(:Field)`)
- Ollama native `/api/chat` for generator (do NOT switch to `/v1`)
- Session keyed by string `session_id` in process-local dict in `api.py`

---

## 10. Future Work (Deferred, Do Not Implement Now)

- **Option B graph schema**: typed node graph with `(:NegativeThought)`, `(:Emotion)`, `(:Reframe)` nodes and semantic edges
- **Qwen JSON reliability**: retry wrapper with up to 3 attempts if JSON parse fails
- **`"think": false` performance**: if Qwen thinking tokens cause noticeable latency, add `"think": false` to the `/api/chat` payload options
- **Multi-session UI**: session selector in Gradio header
- **Session export**: save session transcript + graph snapshot to JSON file
- **WebSocket push**: replace polling with WS for true real-time graph updates

---

## 11. Running the System

```bash
# 1. Start Ollama with Qwen
ollama pull qwen3:8b
ollama serve   # runs on localhost:11434

# 2. (Optional) Start Neo4j
docker run -p7474:7474 -p7687:7687 \
  -e NEO4J_AUTH=neo4j/changeme \
  neo4j:5

# 3. Configure
cp .env.example .env
# Edit .env: set GRAPH_BACKEND=neo4j if using Neo4j

# 4. Install and run
pip install -r requirements.txt
uvicorn api:app --reload

# 5. Open
# http://localhost:8000/  → Gradio UI (chat + live graph side by side)
```

---

*Note for Option B (future): when moving to a typed node graph, `interfaces.py`'s `GraphStore` protocol will need `add_node(type, properties)` and `add_edge(from_id, to_id, rel_type)` methods, and the `/graph` endpoint will return richer Cytoscape data. The Cytoscape panel is already compatible — just update the color map.*
