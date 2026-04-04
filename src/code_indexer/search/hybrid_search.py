"""
Hybrid search engine combining BM25, vector search, and graph traversal.

Uses Reciprocal Rank Fusion (RRF) to merge results from heterogeneous
search backends into a single ranked list.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    ranked_lists: List[List[Dict]],
    id_key: str = "element_id",
    k: int = 60,
) -> List[Dict]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    RRF score = Σ 1 / (k + rank_position) for each list

    Args:
        ranked_lists: List of ranked result lists.
        id_key: Key to identify unique results.
        k: RRF parameter (higher = more weight to rank, lower = more to score).

    Returns:
        Merged and re-ranked results.
    """
    fused_scores: Dict[str, float] = {}
    element_data: Dict[str, Dict] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list):
            eid = result.get(id_key) or result.get("id", "")
            if not eid:
                continue

            rrf_score = 1.0 / (k + rank + 1)
            fused_scores[eid] = fused_scores.get(eid, 0.0) + rrf_score

            # Keep the richest metadata version
            if eid not in element_data or len(str(result)) > len(
                str(element_data[eid])
            ):
                element_data[eid] = result

    # Sort by fused score
    sorted_ids = sorted(
        fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True
    )

    results = []
    for eid in sorted_ids:
        data = element_data[eid].copy()
        data["rrf_score"] = fused_scores[eid]
        results.append(data)

    return results


class HybridSearchEngine:
    """Combines BM25, vector search, and graph queries for comprehensive code search."""

    def __init__(
        self,
        bm25_index=None,
        milvus_store=None,
        encoder=None,
        graph_queries=None,
        bm25_weight: float = 0.5,
        vector_weight: float = 0.5,
    ):
        self.bm25_index = bm25_index
        self.milvus_store = milvus_store
        self.encoder = encoder
        self.graph_queries = graph_queries
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight

    def search(
        self,
        query: str,
        top_k: int = 10,
        repo_name: str = "",
        use_bm25: bool = True,
        use_vector: bool = True,
        use_graph: bool = True,
        rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        """Execute a hybrid search across all backends.

        Args:
            query: Search query (natural language or code).
            top_k: Number of final results.
            repo_name: Optional repository filter.
            use_bm25: Include BM25 results.
            use_vector: Include vector search results.
            use_graph: Include graph search results.
            rerank: Whether to apply reranking (handled externally).

        Returns:
            Ranked list of search results with metadata.
        """
        ranked_lists = []
        retrieval_count = top_k * 5  # Retrieve more for fusion

        # ── BM25 Search ────────────────────────────────────────────────
        if use_bm25 and self.bm25_index:
            try:
                bm25_results = self.bm25_index.search(query, top_k=retrieval_count)
                if repo_name:
                    bm25_results = [
                        r for r in bm25_results if r.get("repo_name") == repo_name
                    ]
                if bm25_results:
                    ranked_lists.append(bm25_results)
                    logger.debug(f"BM25 returned {len(bm25_results)} results")
            except Exception as e:
                logger.warning(f"BM25 search failed: {e}")

        # ── Vector Search ──────────────────────────────────────────────
        if use_vector and self.milvus_store and self.encoder:
            try:
                query_embedding = self.encoder.encode(query)
                if repo_name:
                    vector_results = self.milvus_store.search_by_repo(
                        query_embedding, repo_name, top_k=retrieval_count
                    )
                else:
                    vector_results = self.milvus_store.search(
                        query_embedding, top_k=retrieval_count
                    )
                # Normalize: map "id" to "element_id" for fusion
                for r in vector_results:
                    if "id" in r and "element_id" not in r:
                        r["element_id"] = r["id"]
                if vector_results:
                    ranked_lists.append(vector_results)
                    logger.debug(
                        f"Vector search returned {len(vector_results)} results"
                    )
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # ── Graph Search ───────────────────────────────────────────────
        if use_graph and self.graph_queries:
            try:
                # Try exact name match first
                graph_results = self.graph_queries.search_by_pattern(
                    query, repo_name=repo_name
                )
                if graph_results:
                    # Convert graph results to standard format
                    formatted = []
                    for r in graph_results:
                        node = r.get("e", {})
                        if hasattr(node, "__getitem__"):
                            formatted.append(
                                {
                                    "element_id": node.get("element_id", ""),
                                    "name": node.get("name", ""),
                                    "qualified_name": node.get("qualified_name", ""),
                                    "file_path": node.get("file_path", ""),
                                    "element_type": node.get("element_type", ""),
                                    "start_line": node.get("start_line", 0),
                                    "end_line": node.get("end_line", 0),
                                    "code": node.get("code", ""),
                                    "signature": node.get("signature", ""),
                                    "description": node.get("description", ""),
                                    "repo_name": node.get("repo_name", ""),
                                    "language": node.get("language", ""),
                                    "parent_class": node.get("parent_class", ""),
                                    "complexity": node.get("complexity", 0),
                                }
                            )
                    if formatted:
                        ranked_lists.append(formatted)
                        logger.debug(f"Graph search returned {len(formatted)} results")
            except Exception as e:
                logger.warning(f"Graph search failed: {e}")

        # ── Fusion ─────────────────────────────────────────────────────
        if not ranked_lists:
            return []

        if len(ranked_lists) == 1:
            # Single source, no need for RRF
            fused = ranked_lists[0][: top_k * 3]
        else:
            fused = reciprocal_rank_fusion(ranked_lists)

        # Return more than top_k to allow for reranking downstream
        return fused[: top_k * 3 if rerank else top_k]

    def search_exact(self, name: str, repo_name: str = "") -> List[Dict]:
        """Search for an exact function/class name."""
        results = []

        # BM25 exact match
        if self.bm25_index:
            bm25_results = self.bm25_index.search(name, top_k=20)
            exact = [r for r in bm25_results if r.get("name") == name]
            if exact:
                results.extend(exact)
            else:
                results.extend(bm25_results[:5])

        # Graph exact match
        if self.graph_queries:
            graph_results = self.graph_queries.find_by_name(name, repo_name)
            for r in graph_results:
                node = r.get("e", {})
                if hasattr(node, "__getitem__"):
                    results.append(
                        {
                            "element_id": node.get("element_id", ""),
                            "name": node.get("name", ""),
                            "qualified_name": node.get("qualified_name", ""),
                            "file_path": node.get("file_path", ""),
                            "element_type": node.get("element_type", ""),
                            "start_line": node.get("start_line", 0),
                            "end_line": node.get("end_line", 0),
                            "code": node.get("code", ""),
                            "signature": node.get("signature", ""),
                            "description": node.get("description", ""),
                            "repo_name": node.get("repo_name", ""),
                            "language": node.get("language", ""),
                        }
                    )

        return results
