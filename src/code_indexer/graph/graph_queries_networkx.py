"""
Graph queries implemented purely in Python using NetworkX.

Provides the same interface as GraphQueries but operates on the in-memory
NetworkX graph instead of using Cypher.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import networkx as nx

logger = logging.getLogger(__name__)


class GraphQueriesNetworkx:
    """Execute code analysis queries against a NetworkX graph."""

    def __init__(self, nx_store):
        self.store = nx_store

    @property
    def graph(self):
        return self.store.graph

    def _node_to_dict(self, node_data: Dict) -> Dict:
        """Helper to return node data cleanly."""
        # Create a shallow copy to safely mutate
        d = dict(node_data.items())
        # Return inside a dict structured like the cypher results where 'e' contains the node
        return {"e": d}

    # ── Lookup ──────────────────────────────────────────────────────────

    def find_by_name(self, name: str, repo_name: str = "") -> List[Dict]:
        results = []
        for n, d in self.graph.nodes(data=True):
            if d.get("label") == "Repository":
                continue
            if d.get("name") == name or d.get("qualified_name") == name:
                if not repo_name or d.get("repo_name") == repo_name:
                    results.append(self._node_to_dict(d))
        return sorted(results, key=lambda x: (x["e"].get("element_type", ""), x["e"].get("name", "")))

    def search_by_pattern(self, pattern: str, repo_name: str = "") -> List[Dict]:
        pattern = pattern.lower()
        results = []
        for n, d in self.graph.nodes(data=True):
            if d.get("label") == "Repository":
                continue
            node_name = str(d.get("name", "")).lower()
            if pattern in node_name:
                if not repo_name or d.get("repo_name") == repo_name:
                    results.append(self._node_to_dict(d))
                    if len(results) >= 50:
                        break
        return sorted(results, key=lambda x: x["e"].get("name", ""))

    # ── Callers & Callees ───────────────────────────────────────────────

    def find_callers(self, name: str, repo_name: str = "") -> List[Dict]:
        targets = [
            n for n, d in self.graph.nodes(data=True)
            if (d.get("name") == name or d.get("qualified_name") == name)
            and (not repo_name or d.get("repo_name") == repo_name)
        ]

        seen: set = set()
        results = []
        for target in targets:
            callee_data = self.graph.nodes[target]
            for u, _v, _key, edge_data in self.graph.in_edges(target, data=True, keys=True):
                if edge_data.get("type") != "CALLS":
                    continue
                caller_data = self.graph.nodes[u]
                dedupe_key = (
                    caller_data.get("qualified_name") or caller_data.get("name"),
                    caller_data.get("file_path"),
                    caller_data.get("start_line"),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append({
                    "caller_name": caller_data.get("name"),
                    "caller_qualified_name": caller_data.get("qualified_name"),
                    "caller_type": caller_data.get("element_type"),
                    "caller_file": caller_data.get("file_path"),
                    "caller_line": caller_data.get("start_line"),
                    "callee_name": callee_data.get("name"),
                })
        return sorted(results, key=lambda x: (x.get("caller_file", ""), x.get("caller_line", 0)))

    def find_callees(self, name: str, repo_name: str = "") -> List[Dict]:
        callers = [
            n for n, d in self.graph.nodes(data=True)
            if (d.get("name") == name or d.get("qualified_name") == name)
            and (not repo_name or d.get("repo_name") == repo_name)
        ]

        seen: set = set()
        results = []
        for caller in callers:
            caller_data = self.graph.nodes[caller]
            for _u, v, _key, edge_data in self.graph.out_edges(caller, data=True, keys=True):
                if edge_data.get("type") != "CALLS":
                    continue
                callee_data = self.graph.nodes[v]
                dedupe_key = (
                    callee_data.get("qualified_name") or callee_data.get("name"),
                    callee_data.get("file_path"),
                    callee_data.get("start_line"),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append({
                    "callee_name": callee_data.get("name"),
                    "callee_qualified_name": callee_data.get("qualified_name"),
                    "callee_type": callee_data.get("element_type"),
                    "callee_file": callee_data.get("file_path"),
                    "callee_line": callee_data.get("start_line"),
                    "caller_name": caller_data.get("name"),
                })
        return sorted(results, key=lambda x: (x.get("callee_file", ""), x.get("callee_line", 0)))

    # ── Call Chains ─────────────────────────────────────────────────────

    def find_call_chain(self, from_name: str, to_name: str, max_depth: int = 10) -> List[Dict]:
        starts = [n for n, d in self.graph.nodes(data=True) if d.get("name") == from_name or d.get("qualified_name") == from_name]
        ends = [n for n, d in self.graph.nodes(data=True) if d.get("name") == to_name or d.get("qualified_name") == to_name]
        
        if not starts or not ends:
            return []
            
        # Create a subgraph of just CALLS edges
        calls_edges = [(u, v) for u, v, d in self.graph.edges(data=True) if d.get("type") == "CALLS"]
        calls_graph = nx.DiGraph(calls_edges)
        
        # Add any missing nodes to subgraph
        for n in starts + ends:
            if n not in calls_graph:
                calls_graph.add_node(n)
                
        try:
            path = nx.shortest_path(calls_graph, source=starts[0], target=ends[0])
            if len(path) > max_depth + 1:
                return []
                
            chain = []
            for node_id in path:
                d = self.graph.nodes[node_id]
                chain.append({
                    "name": d.get("name"),
                    "qualified_name": d.get("qualified_name"),
                    "file_path": d.get("file_path"),
                    "start_line": d.get("start_line"),
                    "element_type": d.get("element_type"),
                })
            return [{"chain": chain}]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def find_all_callers_recursive(self, name: str, max_depth: int = 5) -> List[Dict]:
        targets = [n for n, d in self.graph.nodes(data=True) if d.get("name") == name or d.get("qualified_name") == name]
        if not targets:
            return []
            
        calls_edges = [(v, u) for u, v, d in self.graph.edges(data=True) if d.get("type") == "CALLS"]
        reverse_calls_graph = nx.DiGraph(calls_edges)
        
        all_callers = set()
        for target in targets:
            if target in reverse_calls_graph:
                # Get nodes within max_depth steps
                lengths = nx.single_source_shortest_path_length(reverse_calls_graph, target, cutoff=max_depth)
                all_callers.update([n for n in lengths.keys() if n != target])
                
        results = []
        for node_id in all_callers:
            d = self.graph.nodes[node_id]
            results.append({
                "name": d.get("name"),
                "qualified_name": d.get("qualified_name"),
                "element_type": d.get("element_type"),
                "file_path": d.get("file_path"),
                "start_line": d.get("start_line"),
            })
        return sorted(results, key=lambda x: (x.get("file_path", ""), x.get("start_line", 0)))

    # ── Test ↔ Source coverage ──────────────────────────────────────────

    def tests_for(self, name: str, repo_name: str = "") -> List[Dict]:
        """Return test elements that exercise the source element `name`.

        Walks `TESTS` in-edges only — direct connections, not transitive,
        because pytest fixtures and test helpers shouldn't be reported as
        "tests" for the underlying source.
        """
        targets = [
            n for n, d in self.graph.nodes(data=True)
            if (d.get("name") == name or d.get("qualified_name") == name)
            and (not repo_name or d.get("repo_name") == repo_name)
        ]
        seen: set = set()
        results: List[Dict] = []
        for target in targets:
            for u, _v, _key, edge_data in self.graph.in_edges(target, data=True, keys=True):
                if edge_data.get("type") != "TESTS":
                    continue
                test_data = self.graph.nodes[u]
                key = (
                    test_data.get("qualified_name") or test_data.get("name"),
                    test_data.get("file_path"),
                    test_data.get("start_line"),
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "test_name": test_data.get("name"),
                    "test_qualified_name": test_data.get("qualified_name"),
                    "test_file": test_data.get("file_path"),
                    "test_line": test_data.get("start_line"),
                    "covers": self.graph.nodes[target].get("name"),
                })
        return sorted(results, key=lambda r: (r.get("test_file", ""), r.get("test_line", 0)))

    def tested_by(self, test_name: str, repo_name: str = "") -> List[Dict]:
        """Return source elements exercised by the test `test_name`."""
        sources = [
            n for n, d in self.graph.nodes(data=True)
            if (d.get("name") == test_name or d.get("qualified_name") == test_name)
            and d.get("is_test")
            and (not repo_name or d.get("repo_name") == repo_name)
        ]
        seen: set = set()
        results: List[Dict] = []
        for src in sources:
            for _u, v, _key, edge_data in self.graph.out_edges(src, data=True, keys=True):
                if edge_data.get("type") != "TESTS":
                    continue
                tgt_data = self.graph.nodes[v]
                key = (
                    tgt_data.get("qualified_name") or tgt_data.get("name"),
                    tgt_data.get("file_path"),
                    tgt_data.get("start_line"),
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "name": tgt_data.get("name"),
                    "qualified_name": tgt_data.get("qualified_name"),
                    "element_type": tgt_data.get("element_type"),
                    "file_path": tgt_data.get("file_path"),
                    "start_line": tgt_data.get("start_line"),
                })
        return sorted(results, key=lambda r: (r.get("file_path", ""), r.get("start_line", 0)))

    # ── Impact Analysis ─────────────────────────────────────────────────

    def impact_analysis(self, name: str, max_depth: int = 3) -> Dict[str, Any]:
        direct = self.find_callers(name)
        all_callers = self.find_all_callers_recursive(name, max_depth)

        affected_files = set()
        for caller in all_callers:
            if caller.get("file_path"):
                affected_files.add(caller["file_path"])

        return {
            "target": name,
            "direct_callers": len(direct),
            "total_affected": len(all_callers),
            "affected_files": sorted(list(affected_files)),
            "direct_caller_details": direct,
            "all_affected_elements": all_callers,
        }

    # ── Dead Code Detection ─────────────────────────────────────────────

    def find_dead_code(self, repo_name: str = "") -> List[Dict]:
        results = []
        calls_graph = nx.DiGraph([(u, v) for u, v, d in self.graph.edges(data=True) if d.get("type") == "CALLS"])
        
        for n, d in self.graph.nodes(data=True):
            if d.get("label") == "Repository" or d.get("element_type") not in ['function', 'method']:
                continue
                
            if repo_name and d.get("repo_name") != repo_name:
                continue
                
            name = d.get("name", "")
            if name in ['main', '__init__', 'setUp', 'tearDown', '__enter__', '__exit__'] or name.startswith('test_') or name.startswith('__'):
                continue
                
            # Node is implicitly dead if it's not even in the calls graph, 
            # or if it has in_degree of 0 in it.
            if n not in calls_graph or calls_graph.in_degree(n) == 0:
                results.append({
                    "name": name,
                    "qualified_name": d.get("qualified_name"),
                    "file_path": d.get("file_path"),
                    "start_line": d.get("start_line"),
                    "element_type": d.get("element_type"),
                    "complexity": d.get("complexity"),
                })
                
        return sorted(results, key=lambda x: (x.get("file_path", ""), x.get("start_line", 0)))

    # ── Statistics ──────────────────────────────────────────────────────

    def get_stats(self, repo_name: str = "") -> Dict[str, Any]:
        total_elements = 0
        functions = 0
        methods = 0
        classes = 0
        languages = set()
        files = set()
        
        for n, d in self.graph.nodes(data=True):
            if d.get("label") == "Repository":
                continue
                
            if repo_name and d.get("repo_name") != repo_name:
                continue
                
            total_elements += 1
            etype = d.get("element_type")
            if etype == "function":
                functions += 1
            elif etype == "method":
                methods += 1
            elif etype == "class":
                classes += 1
                
            lang = d.get("language")
            if lang:
                languages.add(lang)
                
            path = d.get("file_path")
            if path:
                files.add(path)
                
        return {
            "total_elements": total_elements,
            "functions": functions,
            "methods": methods,
            "classes": classes,
            "languages": list(languages),
            "total_files": len(files),
        }
