"""
NetworkX in-memory graph store for code relationships.

Provides a lightweight, zero-setup alternative to Neo4j that stores
nodes and edges in memory and can persist them to disk.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List

import networkx as nx

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)


class NetworkxStore:
    """NetworkX graph database interface for code elements."""

    def __init__(self, persist_path: str = "./.codeindexer_cache/graph.pkl"):
        self.persist_path = Path(persist_path)
        self.graph = nx.MultiDiGraph()
        self._load()
        logger.info(f"Initialized in-memory NetworkX graph store")

    def _load(self):
        """Load graph from disk if it exists."""
        if self.persist_path.exists():
            try:
                with open(self.persist_path, "rb") as f:
                    self.graph = pickle.load(f)
                logger.debug(f"Loaded graph with {self.graph.number_of_nodes()} nodes")
            except Exception as e:
                logger.error(f"Failed to load graph: {e}")
                self.graph = nx.MultiDiGraph()

    def _save(self):
        """Save graph to disk."""
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.persist_path, "wb") as f:
                pickle.dump(self.graph, f)
        except Exception as e:
            logger.error(f"Failed to save graph: {e}")

    def close(self):
        """Close connection (just save)."""
        self._save()

    def clear_repository(self, repo_name: str):
        """Remove all nodes and relationships for a repository."""
        nodes_to_remove = [
            n for n, d in self.graph.nodes(data=True)
            if d.get("repo_name") == repo_name or (d.get("label") == "Repository" and d.get("name") == repo_name)
        ]
        self.graph.remove_nodes_from(nodes_to_remove)
        logger.info(f"Cleared graph data for repository: {repo_name}")
        self._save()

    def store_elements(self, elements: List[CodeElement]) -> dict:
        """Store a batch of code elements and their relationships."""
        if not elements:
            return {"nodes": 0, "relationships": 0}

        nodes_created = 0
        rels_created = 0

        # Create Repository nodes
        repo_names = set(el.repo_name for el in elements if el.repo_name)
        for repo_name in repo_names:
            repo_id = f"repo:{repo_name}"
            if not self.graph.has_node(repo_id):
                self.graph.add_node(repo_id, label="Repository", name=repo_name)
                nodes_created += 1

        # Create CodeElement nodes
        for el in elements:
            params = self._element_to_params(el)
            self.graph.add_node(el.element_id, label=params["element_type_label"], **params)
            nodes_created += 1

            # Repo -> File
            repo_id = f"repo:{el.repo_name}"
            self.graph.add_edge(repo_id, el.element_id, type="CONTAINS_FILE")
            rels_created += 1

        # Create relationships
        element_by_name: dict[str, str] = {}
        class_elements = {}
        for el in elements:
            element_by_name[el.name] = el.element_id
            if el.qualified_name:
                element_by_name[el.qualified_name] = el.element_id
            if el.element_type == "class":
                class_elements[el.name] = el.element_id

        for el in elements:
            # Class -> Method
            if el.parent_element_id and self.graph.has_node(el.parent_element_id):
                self.graph.add_edge(el.parent_element_id, el.element_id, type="HAS_METHOD")
                rels_created += 1

            # Calls
            for call_name in el.calls:
                target_id = element_by_name.get(call_name)
                if not target_id:
                    short_name = call_name.split(".")[-1] if "." in call_name else None
                    if short_name:
                        target_id = element_by_name.get(short_name)

                if target_id and target_id != el.element_id:
                    self.graph.add_edge(el.element_id, target_id, type="CALLS")
                    rels_created += 1

            # Inherits
            if el.element_type == "class" and el.inherits_from:
                for parent_name in el.inherits_from:
                    parent_id = class_elements.get(parent_name)
                    if parent_id:
                        self.graph.add_edge(el.element_id, parent_id, type="INHERITS")
                        rels_created += 1

        self._save()
        logger.info(f"Stored {nodes_created} nodes, {rels_created} relationships in NetworkX")
        return {"nodes": nodes_created, "relationships": rels_created}

    def _element_to_params(self, el: CodeElement) -> dict:
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
            "code": el.code,
            "signature": el.signature,
            "description": el.description,
            "docstring": el.docstring,
            "parent_class": el.parent_class or "",
            "complexity": el.complexity,
            "line_count": el.line_count,
        }

    def list_repositories(self) -> List[Dict[str, Any]]:
        """List all indexed repositories."""
        repos = []
        for n, d in self.graph.nodes(data=True):
            if d.get("label") == "Repository":
                repo_name = d.get("name")
                # Count elements for this repo
                count = sum(1 for _, ed in self.graph.nodes(data=True) if ed.get("repo_name") == repo_name)
                repos.append({
                    "name": repo_name,
                    "element_count": count,
                    "updated_at": "In-Memory",
                })
        return sorted(repos, key=lambda x: x["name"])

    def delete_repository(self, repo_name: str):
        """Delete a repository from the graph."""
        self.clear_repository(repo_name)
