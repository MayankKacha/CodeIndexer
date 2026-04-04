"""
Graph queries for code analysis.

Pre-built Cypher queries for common code analysis tasks: callers, callees,
call chains, impact analysis, dead code detection, and more.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphQueries:
    """Execute pre-built graph queries against Neo4j."""

    def __init__(self, driver):
        self.driver = driver

    def _run(self, cypher: str, **params) -> List[Dict[str, Any]]:
        """Run a Cypher query and return results as dicts."""
        with self.driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    # ── Lookup ──────────────────────────────────────────────────────────

    def find_by_name(self, name: str, repo_name: str = "") -> List[Dict]:
        """Find code elements by name (exact or partial match)."""
        if repo_name:
            return self._run(
                """
                MATCH (e:CodeElement)
                WHERE (e.name = $name OR e.qualified_name = $name)
                  AND e.repo_name = $repo_name
                RETURN e
                ORDER BY e.element_type, e.name
                """,
                name=name,
                repo_name=repo_name,
            )
        return self._run(
            """
            MATCH (e:CodeElement)
            WHERE e.name = $name OR e.qualified_name = $name
            RETURN e
            ORDER BY e.element_type, e.name
            """,
            name=name,
        )

    def search_by_pattern(self, pattern: str, repo_name: str = "") -> List[Dict]:
        """Search code elements by name pattern (case-insensitive contains)."""
        cypher = """
            MATCH (e:CodeElement)
            WHERE toLower(e.name) CONTAINS toLower($pattern)
        """
        if repo_name:
            cypher += " AND e.repo_name = $repo_name"
        cypher += " RETURN e ORDER BY e.name LIMIT 50"
        return self._run(cypher, pattern=pattern, repo_name=repo_name)

    # ── Callers & Callees ───────────────────────────────────────────────

    def find_callers(self, name: str, repo_name: str = "") -> List[Dict]:
        """Find all direct callers of a function/method."""
        cypher = """
            MATCH (caller:CodeElement)-[:CALLS]->(callee:CodeElement)
            WHERE callee.name = $name OR callee.qualified_name = $name
        """
        if repo_name:
            cypher += " AND callee.repo_name = $repo_name"
        cypher += """
            RETURN caller.name AS caller_name,
                   caller.qualified_name AS caller_qualified_name,
                   caller.element_type AS caller_type,
                   caller.file_path AS caller_file,
                   caller.start_line AS caller_line,
                   callee.name AS callee_name
            ORDER BY caller.file_path, caller.start_line
        """
        return self._run(cypher, name=name, repo_name=repo_name)

    def find_callees(self, name: str, repo_name: str = "") -> List[Dict]:
        """Find all functions/methods called by a given function."""
        cypher = """
            MATCH (caller:CodeElement)-[:CALLS]->(callee:CodeElement)
            WHERE caller.name = $name OR caller.qualified_name = $name
        """
        if repo_name:
            cypher += " AND caller.repo_name = $repo_name"
        cypher += """
            RETURN callee.name AS callee_name,
                   callee.qualified_name AS callee_qualified_name,
                   callee.element_type AS callee_type,
                   callee.file_path AS callee_file,
                   callee.start_line AS callee_line,
                   caller.name AS caller_name
            ORDER BY callee.file_path, callee.start_line
        """
        return self._run(cypher, name=name, repo_name=repo_name)

    # ── Call Chains ─────────────────────────────────────────────────────

    def find_call_chain(
        self, from_name: str, to_name: str, max_depth: int = 10
    ) -> List[Dict]:
        """Find the call chain between two functions."""
        return self._run(
            """
            MATCH path = shortestPath(
                (start:CodeElement)-[:CALLS*1..{max_depth}]->(end:CodeElement)
            )
            WHERE (start.name = $from_name OR start.qualified_name = $from_name)
              AND (end.name = $to_name OR end.qualified_name = $to_name)
            RETURN [node IN nodes(path) | {
                name: node.name,
                qualified_name: node.qualified_name,
                file_path: node.file_path,
                start_line: node.start_line,
                element_type: node.element_type
            }] AS chain
            LIMIT 5
            """.replace("{max_depth}", str(max_depth)),
            from_name=from_name,
            to_name=to_name,
        )

    def find_all_callers_recursive(
        self, name: str, max_depth: int = 5
    ) -> List[Dict]:
        """Find all direct and indirect callers up to max_depth."""
        return self._run(
            f"""
            MATCH (target:CodeElement)
            WHERE target.name = $name OR target.qualified_name = $name
            WITH target
            MATCH path = (caller:CodeElement)-[:CALLS*1..{max_depth}]->(target)
            UNWIND nodes(path) AS node
            WITH DISTINCT node
            RETURN node.name AS name,
                   node.qualified_name AS qualified_name,
                   node.element_type AS element_type,
                   node.file_path AS file_path,
                   node.start_line AS start_line
            ORDER BY node.file_path, node.start_line
            """,
            name=name,
        )

    # ── Class Hierarchy ─────────────────────────────────────────────────

    def find_class_hierarchy(self, class_name: str) -> List[Dict]:
        """Find the full inheritance hierarchy for a class."""
        return self._run(
            """
            MATCH (c:CodeElement {element_type: 'class'})
            WHERE c.name = $class_name OR c.qualified_name = $class_name
            OPTIONAL MATCH path = (c)-[:INHERITS*0..10]->(parent:CodeElement)
            OPTIONAL MATCH (child:CodeElement)-[:INHERITS*1..10]->(c)
            RETURN c.name AS class_name,
                   collect(DISTINCT parent.name) AS ancestors,
                   collect(DISTINCT child.name) AS descendants
            """,
            class_name=class_name,
        )

    def find_class_methods(self, class_name: str) -> List[Dict]:
        """Find all methods of a class."""
        return self._run(
            """
            MATCH (c:CodeElement {element_type: 'class'})-[:HAS_METHOD]->(m:CodeElement)
            WHERE c.name = $class_name OR c.qualified_name = $class_name
            RETURN m.name AS method_name,
                   m.signature AS signature,
                   m.file_path AS file_path,
                   m.start_line AS start_line,
                   m.end_line AS end_line,
                   m.complexity AS complexity,
                   m.description AS description
            ORDER BY m.start_line
            """,
            class_name=class_name,
        )

    # ── Impact Analysis ─────────────────────────────────────────────────

    def impact_analysis(self, name: str, max_depth: int = 3) -> Dict[str, Any]:
        """Analyze the impact of changing a function/method.

        Returns direct and indirect dependents that would be affected.
        """
        direct = self.find_callers(name)
        all_callers = self.find_all_callers_recursive(name, max_depth)

        # Get unique files affected
        affected_files = set()
        for caller in all_callers:
            if caller.get("file_path"):
                affected_files.add(caller["file_path"])

        return {
            "target": name,
            "direct_callers": len(direct),
            "total_affected": len(all_callers),
            "affected_files": sorted(affected_files),
            "direct_caller_details": direct,
            "all_affected_elements": all_callers,
        }

    # ── Dead Code Detection ─────────────────────────────────────────────

    def find_dead_code(self, repo_name: str = "") -> List[Dict]:
        """Find functions/methods that are never called (potential dead code)."""
        cypher = """
            MATCH (e:CodeElement)
            WHERE e.element_type IN ['function', 'method']
              AND NOT (e)<-[:CALLS]-()
              AND NOT e.name IN ['main', '__init__', 'setUp', 'tearDown',
                                  'test_', '__enter__', '__exit__']
              AND NOT e.name STARTS WITH 'test_'
              AND NOT e.name STARTS WITH '__'
        """
        if repo_name:
            cypher += " AND e.repo_name = $repo_name"
        cypher += """
            RETURN e.name AS name,
                   e.qualified_name AS qualified_name,
                   e.file_path AS file_path,
                   e.start_line AS start_line,
                   e.element_type AS element_type,
                   e.complexity AS complexity
            ORDER BY e.file_path, e.start_line
        """
        return self._run(cypher, repo_name=repo_name)

    # ── Complexity Analysis ─────────────────────────────────────────────

    def find_complex_functions(
        self, threshold: int = 10, limit: int = 20, repo_name: str = ""
    ) -> List[Dict]:
        """Find functions with high cyclomatic complexity."""
        cypher = """
            MATCH (e:CodeElement)
            WHERE e.element_type IN ['function', 'method']
              AND e.complexity >= $threshold
        """
        if repo_name:
            cypher += " AND e.repo_name = $repo_name"
        cypher += """
            RETURN e.name AS name,
                   e.qualified_name AS qualified_name,
                   e.file_path AS file_path,
                   e.start_line AS start_line,
                   e.complexity AS complexity,
                   e.line_count AS line_count
            ORDER BY e.complexity DESC
            LIMIT $limit
        """
        return self._run(cypher, threshold=threshold, limit=limit, repo_name=repo_name)

    # ── Statistics ──────────────────────────────────────────────────────

    def get_stats(self, repo_name: str = "") -> Dict[str, Any]:
        """Get graph statistics for a repository."""
        filter_clause = "WHERE e.repo_name = $repo_name" if repo_name else ""
        result = self._run(
            f"""
            MATCH (e:CodeElement)
            {filter_clause}
            RETURN count(e) AS total_elements,
                   count(CASE WHEN e.element_type = 'function' THEN 1 END) AS functions,
                   count(CASE WHEN e.element_type = 'method' THEN 1 END) AS methods,
                   count(CASE WHEN e.element_type = 'class' THEN 1 END) AS classes,
                   collect(DISTINCT e.language) AS languages,
                   collect(DISTINCT e.file_path) AS files
            """,
            repo_name=repo_name,
        )
        if result:
            r = result[0]
            return {
                "total_elements": r.get("total_elements", 0),
                "functions": r.get("functions", 0),
                "methods": r.get("methods", 0),
                "classes": r.get("classes", 0),
                "languages": r.get("languages", []),
                "total_files": len(r.get("files", [])),
            }
        return {}
