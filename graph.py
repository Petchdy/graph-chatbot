"""Swappable graph store implementations.

InMemoryGraphStore - no external dependency, default for demo and tests.
Neo4jGraphStore    - production backend. ALL Cypher lives here.

Both derive their slot set from the injected Schema; field keys are never hardcoded.
"""

from interfaces import Schema

_SESSION_KEYS = {"session_phase", "active_technique"}


class InMemoryGraphStore:

    def __init__(self, schema: Schema):
        self._schema = schema
        self._fields_by_priority = sorted(schema.fields(), key=lambda f: f.priority)
        self._state: dict[str, dict] = {}
        self.reset()

    def reset(self) -> None:
        self._state = {
            f.key: {"value": None, "acquired": False, "turns": []}
            for f in self._fields_by_priority
        }

    def apply_deltas(self, deltas: dict[str, str], turn_id: int) -> None:
        for key, value in deltas.items():
            if key not in self._state:
                continue
            entry = self._state[key]
            entry["value"] = value
            entry["acquired"] = True
            entry["turns"].append(turn_id)

    def missing(self) -> list[str]:
        return [
            f.key for f in self._fields_by_priority
            if not self._state[f.key]["acquired"] and f.priority > 0
        ]

    def acquired_summary(self) -> str:
        acquired = [
            f"{key}={entry['value']}"
            for key, entry in self._state.items()
            if entry["acquired"] and key not in _SESSION_KEYS
        ]
        return ", ".join(acquired) if acquired else "(nothing acquired yet)"

    def snapshot(self) -> dict:
        return {
            key: {"value": entry["value"], "acquired": entry["acquired"]}
            for key, entry in self._state.items()
        }

    def cbt_context(self) -> str:
        phase = (self._state.get("session_phase") or {}).get("value") or "Rapport"
        technique = (self._state.get("active_technique") or {}).get("value") or "none yet"
        acquired_lines = [
            f'  {key}="{entry["value"]}"'
            for key, entry in self._state.items()
            if entry["acquired"] and key not in _SESSION_KEYS
        ]
        missing_keys = self.missing()
        return (
            f"Session phase: {phase}\n"
            f"Active CBT technique: {technique}\n"
            f"What we know so far:\n"
            + ("\n".join(acquired_lines) if acquired_lines else "  (nothing yet)") + "\n"
            + f"Still to explore (soft hints, not a checklist): "
            + (", ".join(missing_keys) if missing_keys else "none")
        )

    def apply_session_state(self, phase: str, technique: str) -> None:
        for key, value in (("session_phase", phase), ("active_technique", technique)):
            if key in self._state:
                self._state[key]["value"] = value
                self._state[key]["acquired"] = True


class Neo4jGraphStore:
    """Neo4j-backed graph store.

    Graph model:
      (:Session {id}) -[:HAS_FIELD]-> (:Field {key, value, acquired, priority})
      (:Field)-[:ACQUIRED_FROM]->(:Turn {id}) evidence edges.
    """

    def __init__(self, schema: Schema, uri: str, user: str, password: str, session_id: str = "default"):
        from neo4j import GraphDatabase
        self._schema = schema
        self._fields_by_priority = sorted(schema.fields(), key=lambda f: f.priority)
        self._session_id = session_id
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self.reset()

    def close(self) -> None:
        self._driver.close()

    def reset(self) -> None:
        with self._driver.session() as session:
            session.run(
                "MATCH (s:Session {id: $sid}) "
                "OPTIONAL MATCH (s)-[:HAS_FIELD]->(f:Field) "
                "OPTIONAL MATCH (f)-[:ACQUIRED_FROM]->(t:Turn) "
                "DETACH DELETE s, f, t",
                sid=self._session_id,
            )
            session.run("MERGE (s:Session {id: $sid})", sid=self._session_id)
            for f in self._fields_by_priority:
                session.run(
                    """
                    MATCH (s:Session {id: $sid})
                    MERGE (s)-[:HAS_FIELD]->(field:Field {key: $key})
                    SET field.value = null, field.acquired = false, field.priority = $priority
                    """,
                    sid=self._session_id, key=f.key, priority=f.priority,
                )

    def apply_deltas(self, deltas: dict[str, str], turn_id: int) -> None:
        if not deltas:
            return
        with self._driver.session() as session:
            session.run("MERGE (t:Turn {id: $turn_id})", turn_id=turn_id)
            for key, value in deltas.items():
                session.run(
                    """
                    MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(field:Field {key: $key})
                    MATCH (t:Turn {id: $turn_id})
                    SET field.value = $value, field.acquired = true
                    MERGE (field)-[:ACQUIRED_FROM]->(t)
                    """,
                    sid=self._session_id, key=key, value=value, turn_id=turn_id,
                )

    def missing(self) -> list[str]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(field:Field)
                WHERE field.acquired = false AND field.priority > 0
                RETURN field.key AS key ORDER BY field.priority ASC
                """,
                sid=self._session_id,
            )
            return [r["key"] for r in result]

    def acquired_summary(self) -> str:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(field:Field)
                WHERE field.acquired = true AND field.priority > 0
                RETURN field.key AS key, field.value AS value ORDER BY field.priority ASC
                """,
                sid=self._session_id,
            )
            acquired = [f"{r['key']}={r['value']}" for r in result]
            return ", ".join(acquired) if acquired else "(nothing acquired yet)"

    def snapshot(self) -> dict:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(field:Field)
                RETURN field.key AS key, field.value AS value,
                       field.acquired AS acquired, field.priority AS priority
                ORDER BY field.priority ASC
                """,
                sid=self._session_id,
            )
            return {
                r["key"]: {"value": r["value"], "acquired": r["acquired"]}
                for r in result
            }

    def cbt_context(self) -> str:
        with self._driver.session() as session:
            state_result = session.run(
                """
                MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(f:Field)
                WHERE f.key IN ['session_phase', 'active_technique']
                RETURN f.key AS key, f.value AS value
                """,
                sid=self._session_id,
            )
            state = {r["key"]: r["value"] for r in state_result}

            acquired_result = session.run(
                """
                MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(f:Field)
                WHERE f.acquired = true AND f.priority > 0
                RETURN f.key AS key, f.value AS value ORDER BY f.priority ASC
                """,
                sid=self._session_id,
            )
            acquired_lines = [f'  {r["key"]}="{r["value"]}"' for r in acquired_result]

        phase = state.get("session_phase") or "Rapport"
        technique = state.get("active_technique") or "none yet"
        missing_keys = self.missing()
        return (
            f"Session phase: {phase}\n"
            f"Active CBT technique: {technique}\n"
            f"What we know so far:\n"
            + ("\n".join(acquired_lines) if acquired_lines else "  (nothing yet)") + "\n"
            + f"Still to explore (soft hints, not a checklist): "
            + (", ".join(missing_keys) if missing_keys else "none")
        )

    def apply_session_state(self, phase: str, technique: str) -> None:
        with self._driver.session() as session:
            for key, value in (("session_phase", phase), ("active_technique", technique)):
                session.run(
                    """
                    MATCH (s:Session {id: $sid})-[:HAS_FIELD]->(f:Field {key: $key})
                    SET f.value = $value, f.acquired = true
                    """,
                    sid=self._session_id, key=key, value=value,
                )
