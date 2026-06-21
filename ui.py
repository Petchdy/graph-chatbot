"""Gradio UI — two-column: chat (left) + live Cytoscape graph (right)."""

import json

import gradio as gr

import factory
from orchestrator import Session, turn

_SESSION_KEYS = {"session_phase", "active_technique"}

INTRO = (
    "Hello, and welcome. I'm glad you're here today. "
    "This is a safe space to talk about whatever is on your mind. "
    "What's been weighing on you lately, or what would you most like to explore today?"
)

CYTOSCAPE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"

NODE_STYLES = json.dumps([
    {"selector": 'node[type="session"]',
     "style": {"background-color": "#2d6a4f", "color": "#fff", "label": "data(label)",
                "text-wrap": "wrap", "text-valign": "center", "font-size": "11px",
                "width": 80, "height": 80, "shape": "ellipse"}},
    {"selector": 'node[type="field"]',
     "style": {"background-color": "#74c69d", "label": "data(label)",
                "text-wrap": "wrap", "text-valign": "center", "font-size": "10px",
                "width": 70, "height": 70}},
    {"selector": 'node[type="session_state"]',
     "style": {"background-color": "#f4a261", "label": "data(label)",
                "text-wrap": "wrap", "text-valign": "center", "font-size": "10px",
                "width": 70, "height": 70, "shape": "diamond"}},
    {"selector": 'node[type="missing"]',
     "style": {"background-color": "#dee2e6", "label": "data(label)",
                "text-valign": "center", "font-size": "9px",
                "width": 50, "height": 50,
                "border-style": "dashed", "border-color": "#adb5bd", "border-width": 2}},
    {"selector": "edge",
     "style": {"label": "data(label)", "font-size": "8px", "curve-style": "bezier",
                "target-arrow-shape": "triangle", "line-color": "#adb5bd",
                "target-arrow-color": "#adb5bd", "arrow-scale": 0.7}},
])


def _build_elements(snapshot: dict) -> list:
    elements = [{"data": {"id": "session", "label": "Session", "type": "session"}}]
    for key, entry in snapshot.items():
        if key in _SESSION_KEYS:
            ntype = "session_state"
        elif entry["acquired"]:
            ntype = "field"
        else:
            ntype = "missing"
        label = f"{key}\n{str(entry['value'])[:20]}" if entry["acquired"] else key
        elements.append({"data": {"id": f"f_{key}", "label": label, "type": ntype}})
        elements.append({"data": {
            "source": "session", "target": f"f_{key}",
            "label": "HAS" if entry["acquired"] else "MISSING",
        }})
    return elements


def _render_graph(snapshot: dict) -> str:
    elements_json = json.dumps(_build_elements(snapshot))
    styles_json = NODE_STYLES
    
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
<script src="{CYTOSCAPE_CDN}"></script>
</head>
<body style="margin:0; padding:0; background:#fafafa;">
<div id="cy" style="width:100%;height:470px;background:#fafafa;
     border:1px solid #dee2e6;border-radius:8px;box-sizing:border-box;"></div>
<script>
(function() {{
  function init() {{
    var el = document.getElementById('cy');
    if (!el || typeof cytoscape === 'undefined') {{
      setTimeout(init, 100); return;
    }}
    el.innerHTML = '';
    cytoscape({{
      container: el,
      style: {styles_json},
      layout: {{ name: 'cose', animate: false, randomize: false, nodeRepulsion: 8000 }},
      elements: {elements_json}
    }}).fit(undefined, 20);
  }}
  init();
}})();
</script>
</body>
</html>
"""
    import html
    escaped_html = html.escape(html_content)
    return f'<iframe srcdoc="{escaped_html}" style="width:100%; height:480px; border:none; border-radius:8px;"></iframe>'


def _new_session() -> Session:
    schema = factory.make_schema()
    return Session(
        schema=schema,
        graph=factory.make_graph(schema),
        extractor=factory.make_extractor(),
        generator=factory.make_generator(),
    )


def _add_user(message: str, history: list):
    return history + [{"role": "user", "content": message}], "", message


def _bot_respond(message: str, history: list, session: Session):
    if session is None:
        session = _new_session()
    result = turn(session, message)
    history = history + [{"role": "assistant", "content": result["reply"]}]
    graph_html = _render_graph(result["slots"])
    return history, session, result["phase"], result["technique"], graph_html


def _reset():
    session = _new_session()
    history = [{"role": "assistant", "content": INTRO}]
    graph_html = _render_graph(session.graph.snapshot())
    return history, session, "Rapport", "—", graph_html


with gr.Blocks(title="CACTUS CBT Therapy", fill_height=True) as demo:
    session_state = gr.State(None)
    pending_msg = gr.State("")

    with gr.Row(equal_height=True):
        with gr.Column(scale=3):
            gr.Markdown("## CACTUS CBT Therapy")
            with gr.Row():
                phase_box = gr.Textbox(label="Phase", value="Rapport",
                                       interactive=False, scale=1)
                technique_box = gr.Textbox(label="Technique", value="—",
                                           interactive=False, scale=3)
            chatbot = gr.Chatbot(height=400)
            with gr.Row():
                msg_box = gr.Textbox(placeholder="Share what's on your mind…",
                                     show_label=False, scale=5)
                send_btn = gr.Button("Send", variant="primary", scale=1)
            reset_btn = gr.Button("New session")

        with gr.Column(scale=2):
            gr.Markdown("## Knowledge Graph")
            graph_panel = gr.HTML()

    _outputs = [chatbot, session_state, phase_box, technique_box, graph_panel]

    send_btn.click(
        _add_user, [msg_box, chatbot], [chatbot, msg_box, pending_msg]
    ).then(
        _bot_respond, [pending_msg, chatbot, session_state], _outputs
    )
    msg_box.submit(
        _add_user, [msg_box, chatbot], [chatbot, msg_box, pending_msg]
    ).then(
        _bot_respond, [pending_msg, chatbot, session_state], _outputs
    )
    reset_btn.click(_reset, [], _outputs)
    demo.load(_reset, [], _outputs)
