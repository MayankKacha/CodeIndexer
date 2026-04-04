"""
Cross-encoder re-ranker for improving search precision.

Takes hybrid search candidates and re-scores them using a cross-encoder
model that processes query-document pairs simultaneously for higher accuracy.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Reranker:
    """Cross-encoder based re-ranker for search results."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: str = "cpu",
    ):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load_model(self):
        """Lazy-load the cross-encoder model."""
        if self._model is not None:
            return

        from sentence_transformers import CrossEncoder

        logger.info(f"Loading re-ranker model: {self.model_name}")
        self._model = CrossEncoder(self.model_name, device=self.device)
        logger.info("Re-ranker model loaded")

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: int = 10,
        text_key: str = "code",
    ) -> List[Dict[str, Any]]:
        """Re-rank search results using the cross-encoder.

        Args:
            query: The original search query.
            results: Candidate results from hybrid search.
            top_k: Number of results to return after re-ranking.
            text_key: Which field to use as the document text for re-ranking.

        Returns:
            Re-ranked results with rerank_score added.
        """
        if not results:
            return []

        if len(results) <= 1:
            return results[:top_k]

        self._load_model()

        # Build query-document pairs
        pairs = []
        valid_indices = []

        for i, result in enumerate(results):
            # Build a comprehensive text representation for reranking
            doc_parts = []
            if result.get("name"):
                doc_parts.append(f"Name: {result['name']}")
            if result.get("element_type"):
                doc_parts.append(f"Type: {result['element_type']}")
            if result.get("description"):
                doc_parts.append(f"Description: {result['description']}")
            if result.get("signature"):
                doc_parts.append(f"Signature: {result['signature']}")
            if result.get(text_key):
                # Limit code length for reranker (max ~512 tokens)
                code = result[text_key]
                if len(code) > 1500:
                    code = code[:1500] + "..."
                doc_parts.append(code)

            doc_text = "\n".join(doc_parts) if doc_parts else str(result)

            pairs.append([query, doc_text])
            valid_indices.append(i)

        # Score all pairs
        try:
            scores = self._model.predict(pairs)
        except Exception as e:
            logger.error(f"Re-ranking failed: {e}")
            return results[:top_k]

        # Attach scores and sort
        scored_results = []
        for score, idx in zip(scores, valid_indices):
            result = results[idx].copy()
            result["rerank_score"] = float(score)
            scored_results.append(result)

        scored_results.sort(key=lambda x: x["rerank_score"], reverse=True)

        return scored_results[:top_k]

    def health_check(self) -> bool:
        """Verify the reranker is working."""
        try:
            self._load_model()
            scores = self._model.predict([
                ["find payment function", "def process_payment(amount): pass"]
            ])
            return len(scores) == 1
        except Exception as e:
            logger.error(f"Reranker health check failed: {e}")
            return False
