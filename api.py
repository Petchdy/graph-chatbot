"""FastAPI backend. Depends only on interfaces.py and factory.py."""

from fastapi import FastAPI
from pydantic import BaseModel

import factory
from orchestrator import Session, turn

app = FastAPI(title="CACTUS CBT Chatbot")

_sessions: dict[str, Session] = {}

_SESSION_KEYS = {"session_phase", "active_technique"}


def _get_or_create(session_id: str) -> Session:
    if session_id not in _sessions:
        schema = factory.make_schema()
        _sessions[session_id] = Session(
            schema=schema,
            graph=factory.make_graph(schema, session_id=session_id),
            extractor=factory.make_extractor(),
            generator=factory.make_generator(),
        )
    return _sessions[session_id]


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    technique: str = ""
    phase: str = ""
    deltas: dict[str, str]
    slots: dict


class ResetRequest(BaseModel):
    session_id: str


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session = _get_or_create(request.session_id)
    result = turn(session, request.message)
    return ChatResponse(**result)


@app.post("/reset")
def reset(request: ResetRequest) -> dict:
    session = _sessions.get(request.session_id)
    if session is not None:
        session.graph.reset()
        session.history.clear()
        session.turn_count = 0
    return {"ok": True}


@app.get("/graph/{session_id}")
def get_graph(session_id: str) -> dict:
    """Cytoscape-compatible node/edge JSON for the session's slot-fill graph."""
    if session_id not in _sessions:
        return {"nodes": [], "edges": []}

    snapshot = _sessions[session_id].graph.snapshot()
    nodes = [{"data": {"id": "session", "label": "Session", "type": "session"}}]
    edges = []

    for key, entry in snapshot.items():
        if key in _SESSION_KEYS:
            ntype = "session_state"
        elif entry["acquired"]:
            ntype = "field"
        else:
            ntype = "missing"

        label = f"{key}\n{str(entry['value'])[:25]}" if entry["acquired"] else key
        nodes.append({"data": {"id": f"f_{key}", "label": label, "type": ntype}})
        edges.append({
            "data": {
                "source": "session",
                "target": f"f_{key}",
                "label": "HAS" if entry["acquired"] else "MISSING",
            }
        })

    return {"nodes": nodes, "edges": edges}


import gradio as gr  # noqa: E402
import ui  # noqa: E402

app = gr.mount_gradio_app(app, ui.demo, path="/")
