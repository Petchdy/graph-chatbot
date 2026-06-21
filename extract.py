"""Swappable extractors.

StubExtractor      - offline key:value regex. Used by tests.
LocalLLMExtractor  - Ollama /api/generate with JSON output.
"""

import json
import re

from prompts import CBT_EXTRACTION_PROMPT


class StubExtractor:
    """Looks for `<field_key>: <value>` lines in the message.

    Offline and deterministic — only used for tests.
    """

    _PATTERN = re.compile(r"(\w+)\s*[:=]\s*(.+)")

    def extract(self, message: str, schema_text: str) -> dict[str, str]:
        known_keys = set(re.findall(r"^- (\w+)", schema_text, flags=re.MULTILINE))
        deltas: dict[str, str] = {}
        for line in message.splitlines():
            m = self._PATTERN.match(line.strip())
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if key in known_keys:
                deltas[key] = value
        return deltas


class LocalLLMExtractor:
    """Extracts CBT field values via a local Ollama model.

    Uses /api/generate with format:'json' to get structured output.
    Unknown keys are silently dropped; JSON parse failures return {}.
    """

    def __init__(self, model: str = "qwen3:8b", host: str = "http://localhost:11434"):
        self._model = model
        self._host = host

    def extract(self, message: str, schema_text: str) -> dict[str, str]:
        import requests

        prompt = CBT_EXTRACTION_PROMPT.format(ontology_schema=schema_text, message=message)
        response = requests.post(
            f"{self._host}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": False,
                  "format": "json", "think": False},
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json().get("response", "{}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[extract] JSON parse failed: {raw[:80]!r}")
            return {}
        known_keys = set(re.findall(r"^- (\w+)", schema_text, flags=re.MULTILINE))
        return {k: str(v) for k, v in parsed.items() if k in known_keys}
