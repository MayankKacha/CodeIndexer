"""
Neo4j graph store for code relationships.

Stores code elements as nodes and their relationships (calls, imports,
inheritance, containment) as edges in a Neo4j graph database.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Neo4j graph database interface for code elements."""

    def __init__(self, uri: str, username: str, password: str):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self._ensure_constraints()
        logger.info(f"Connected to Neo4j at {uri}")

    def close(self):
        """Close the driver connection."""
        self.driver.close()

    def _ensure_constraints(self):
        """Create indexes and constraints for performance."""
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:CodeElement) REQUIRE e.element_id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (e:CodeElement) ON (e.name)",
            "CREATE INDEX IF NOT EXISTS FOR (e:CodeElement) ON (e.file_path)",
            "CREATE INDEX IF NOT EXISTS FOR (e:CodeElement) ON (e.element_type)",
            "CREATE INDEX IF NOT EXISTS FOR (e:CodeElement) ON (e.qualified_name)",
            "CREATE INDEX IF NOT EXISTS FOR (e:CodeElement) ON (e.repo_name)",
        ]
        with self.driver.session() as session:
            for cypher in constraints:
                try:
                    session.run(cypher)
                except Exception as e:
                    logger.debug(f"Constraint/index may already exist: {e}")

    def clear_repository(self, repo_name: str):
        """Remove all nodes and relationships for a repository."""
        with self.driver.session() as session:
            session.run(
                "MATCH (n {repo_name: $repo_name}) DETACH DELETE n",
                repo_name=repo_name,
            )
        logger.info(f"Cleared graph data for repository: {repo_name}")

    def store_elements(self, elements: List[CodeElement]) -> dict:
        """Store a batch of code elements and their relationships in Neo4j.

        Returns:
            Dict with counts of nodes and relationships created.
        """
        if not elements:
            return {"nodes": 0, "relationships": 0}

        nodes_created = 0
        rels_created = 0

        with self.driver.session() as session:
            # ── Create Repository node ──────────────────────────────────
            repo_names = set(el.repo_name for el in elements if el.repo_name)
            for repo_name in repo_names:
                session.run(
                    """
                    MERGE (r:Repository {name: $name})
                    SET r.updated_at = datetime()
                    """,
                    name=repo_name,
                )

            # ── Create CodeElement nodes (batched) ──────────────────────
            batch_size = 100
            for i in range(0, len(elements), batch_size):
                batch = elements[i : i + batch_size]
                params = [self._element_to_params(el) for el in batch]

                result = session.run(
                    """
                    UNWIND $elements AS el
                    MERGE (e:CodeElement {element_id: el.element_id})
                    SET e += el
                    WITH e, el
                    // Add type-specific label
                    CALL apoc.create.addLabels(e, [el.element_type_label])
                    YIELD node
                    RETURN count(node) AS cnt
                    """,
                    elements=params,
                )
                # Fallback if APOC is not installed
                try:
                    record = result.single()
                    nodes_created += record["cnt"] if record else len(batch)
                except Exception:
                    # APOC not available, use simple merge without dynamic labels
                    for el_params in params:
                        session.run(
                            """
                            MERGE (e:CodeElement {element_id: $element_id})
                            SET e.name = $name,
                                e.qualified_name = $qualified_name,
                                e.element_type = $element_type,
                                e.file_path = $file_path,
                                e.repo_name = $repo_name,
                                e.language = $language,
                                e.start_line = $start_line,
                                e.end_line = $end_line,
                                e.signature = $signature,
                                e.description = $description,
                                e.docstring = $docstring,
                                e.parent_class = $parent_class,
                                e.complexity = $complexity,
                                e.line_count = $line_count,
                                e.code = $code
                            """,
                            **el_params,
                        )
                        nodes_created += 1

            # ── Create relationships ────────────────────────────────────

            # 1. Repository → File containment (via CodeElements)
            session.run(
                """
                MATCH (r:Repository), (e:CodeElement)
                WHERE r.name = e.repo_name
                MERGE (r)-[:CONTAINS_FILE]->(e)
                """,
            )

            # 2. Class → Method relationships
            for el in elements:
                if el.parent_element_id:
                    result = session.run(
                        """
                        MATCH (parent:CodeElement {element_id: $parent_id})
                        MATCH (child:CodeElement {element_id: $child_id})
                        MERGE (parent)-[:HAS_METHOD]->(child)
                        RETURN count(*) AS cnt
                        """,
                        parent_id=el.parent_element_id,
                        child_id=el.element_id,
                    )
                    record = result.single()
                    rels_created += record["cnt"] if record else 0

            # 3. Function/Method call relationships
            element_by_name: dict[str, str] = {}
            for el in elements:
                element_by_name[el.name] = el.element_id
                if el.qualified_name:
                    element_by_name[el.qualified_name] = el.element_id

            for el in elements:
                for call_name in el.calls:
                    # Try to match by name
                    target_id = element_by_name.get(call_name)
                    if not target_id:
                        # Try just the last part (e.g., "self.method" → "method")
                        short_name = call_name.split(".")[-1] if "." in call_name else None
                        if short_name:
                            target_id = element_by_name.get(short_name)

                    if target_id and target_id != el.element_id:
                        result = session.run(
                            """
                            MATCH (caller:CodeElement {element_id: $caller_id})
                            MATCH (callee:CodeElement {element_id: $callee_id})
                            MERGE (caller)-[:CALLS]->(callee)
                            RETURN count(*) AS cnt
                            """,
                            caller_id=el.element_id,
                            callee_id=target_id,
                        )
                        record = result.single()
                        rels_created += record["cnt"] if record else 0

            # 4. Inheritance relationships
            class_elements = {el.name: el.element_id for el in elements if el.element_type == "class"}
            for el in elements:
                if el.element_type == "class" and el.inherits_from:
                    for parent_name in el.inherits_from:
                        parent_id = class_elements.get(parent_name)
                        if parent_id:
                            result = session.run(
                                """
                                MATCH (child:CodeElement {element_id: $child_id})
                                MATCH (parent:CodeElement {element_id: $parent_id})
                                MERGE (child)-[:INHERITS]->(parent)
                                RETURN count(*) AS cnt
                                """,
                                child_id=el.element_id,
                                parent_id=parent_id,
                            )
                            record = result.single()
                            rels_created += record["cnt"] if record else 0

        logger.info(f"Stored {nodes_created} nodes, {rels_created} relationships")
        return {"nodes": nodes_created, "relationships": rels_created}

    def _element_to_params(self, el: CodeElement) -> dict:
        """Convert a CodeElement to Neo4j parameters."""
        label_map = {
            "function": "Function",
            "method": "Method",
            "class": "Class",
            "module": "Module",
        }
        return {
            "element_id": el.element_id,
            "name": el.name,
            "qualified_name": el.qualified_name,
            "element_type": el.element_type,
            "element_type_label": label_map.get(el.element_type, "CodeElement"),
            "file_path": el.file_path,
            "repo_name": el.repo_name,
            "language": el.language,
            "start_line": el.start_line,
            "end_line": el.end_line,
            "code": el.code[:50000],  # Limit code size in graph
            "signature": el.signature,
            "description": el.description,
            "docstring": el.docstring[:5000] if el.docstring else "",
            "parent_class": el.parent_class or "",
            "complexity": el.complexity,
            "line_count": el.line_count,
        }

    def list_repositories(self) -> List[Dict[str, Any]]:
        """List all indexed repositories."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (r:Repository)
                OPTIONAL MATCH (r)-[:CONTAINS_FILE]->(e:CodeElement)
                RETURN r.name AS name,
                       r.updated_at AS updated_at,
                       count(DISTINCT e) AS element_count
                ORDER BY r.name
                """
            )
            return [dict(record) for record in result]

    def delete_repository(self, repo_name: str):
        """Delete a repository and all its data from the graph."""
        self.clear_repository(repo_name)
        with self.driver.session() as session:
            session.run(
                "MATCH (r:Repository {name: $name}) DELETE r",
                name=repo_name,
            )
        logger.info(f"Deleted repository: {repo_name}")
