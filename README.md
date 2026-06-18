# Therapist Chatbot Demo (placebo ontology)

A runnable demo of a chatbot that extracts structured information from
conversation into a graph (Neo4j or in-memory) and uses a deterministic
coverage checklist to decide which ontology field to ask about next.

This is a **placebo** demo: the ontology fields (`name`, `emotion`,
`placeholder_a/b/c`) and all prompt content are inert placeholders, not real
clinical content. The point is the architecture: `schema`, `graph`, and
`extract` are swappable behind the contract in `interfaces.py`.

## Architecture

- `interfaces.py` -- the only shared contract (Protocols + `OntologyField`).
  Every other module besides `factory.py` depends only on this.
- `schema.py` -- `PlaceboSchema` (swappable; real ontology drops in here).
- `graph.py` -- `InMemoryGraphStore` (no DB) and `Neo4jGraphStore` (default
  prod backend; all Cypher lives here).
- `extract.py` -- `StubExtractor` (offline `key: value` regex) and
  `LocalLLMExtractor` (local Ollama model; extracts values from natural
  conversational phrasing, not just literal `key: value` syntax, via
  Ollama's native `/api/generate` with JSON-constrained output).
- `generate.py` -- `EchoGenerator` (offline stub), `OpenRouterGenerator`
  (Claude via OpenRouter), and `LocalLLMGenerator` (local Ollama model via
  its native `/api/chat` endpoint -- not the OpenAI-compatible `/v1` one,
  since thinking models like `qwen3.5` only reliably honor `"think": false`
  on the native API).
- `factory.py` -- the only file that imports the concretes above; env vars
  select which implementation is wired in.
- `prompts.py` -- placebo prompt templates with a `{ontology_schema}` slot.
- `orchestrator.py` -- the turn loop: extract -> apply_deltas -> missing() ->
  generate.
- `api.py` -- FastAPI app (`POST /chat`, `POST /reset`); mounts the Gradio UI
  on the same app via `gr.mount_gradio_app`.
- `ui.py` -- Gradio chat UI with a live "Graph state" inspector.

Swapping an implementation means editing only its file plus its one line in
`factory.py`. No other file should need to change.

## Run (zero external services)

```bash
pip install -r requirements.txt
cp .env.example .env   # defaults already run with no Neo4j / no API keys
uvicorn api:app --reload
```

Open http://localhost:8000/ for the Gradio UI, or call the API directly:

```bash
curl -X POST localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "demo", "message": "placeholder_a: hello there"}'
```

## Run with Neo4j

```bash
# in .env:
GRAPH_BACKEND=neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme
```

Start a local Neo4j instance (e.g. `docker run -p7474:7474 -p7687:7687 neo4j`),
export the env vars (or use `python-dotenv` / your shell), then run `uvicorn
api:app --reload` as above.

## Run with local LLM extraction / generation, or OpenRouter generation

```bash
EXTRACTOR=local OLLAMA_MODEL=qwen3.5:9b                  # requires a running Ollama
  OLLAMA_HOST=http://localhost:11434

GENERATOR=openrouter OPENROUTER_API_KEY=...               # requires an OpenRouter key

GENERATOR=local LOCAL_LLM_MODEL=qwen3.5:9b \
  LOCAL_LLM_BASE_URL=http://localhost:11434/v1            # requires a running Ollama
                                                            # (native API only -- not
                                                            # LM Studio or other generic
                                                            # OpenAI-compatible servers)
```

Pull the model first if you haven't: `ollama pull qwen3.5:9b`.

## Tests

```bash
pytest
```

`tests/test_orchestrator.py` runs a scripted conversation through
`orchestrator.turn()` using `InMemoryGraphStore` + `StubExtractor` +
`EchoGenerator`, asserting the graph fills in and `missing()` shrinks --
proving the loop works with zero external services.
