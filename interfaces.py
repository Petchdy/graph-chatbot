"""Stable contract shared by every module.

Only factory.py may import concrete implementations. Everyone else depends only
on the Protocols defined here.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OntologyField:
    key: str
    description: str
    priority: int = 1


@runtime_checkable
class Schema(Protocol):
    def fields(self) -> list[OntologyField]: ...
    def render(self) -> str: ...


@runtime_checkable
class GraphStore(Protocol):
    def apply_deltas(self, deltas: dict[str, str], turn_id: int) -> None: ...
    def missing(self) -> list[str]: ...
    def acquired_summary(self) -> str: ...
    def snapshot(self) -> dict: ...
    def reset(self) -> None: ...
    def cbt_context(self) -> str: ...
    def apply_session_state(self, phase: str, technique: str) -> None: ...


@runtime_checkable
class Extractor(Protocol):
    def extract(self, message: str, schema_text: str) -> dict[str, str]: ...


@runtime_checkable
class Generator(Protocol):
    def generate(self, system: str, history: list[tuple[str, str]]) -> dict: ...
