"""
BM25 index for lexical/keyword search over code elements.

Provides token-based search that excels at finding exact function names,
variable references, and specific code patterns.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    """Tokenize text for BM25 indexing.

    Uses a code-aware tokenizer that splits on camelCase, snake_case,
    punctuation, and whitespace.
    """
    # Split camelCase
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)

    # Split snake_case
    text = text.replace("_", " ")

    # Remove special characters but keep dots for qualified names
    text = re.sub(r"[^\w\s.]", " ", text)

    # Lowercase and split
    tokens = text.lower().split()

    # Remove very short tokens
    tokens = [t for t in tokens if len(t) >= 2]

    return tokens


class BM25Index:
    """BM25-based lexical search index for code elements."""

    def __init__(self):
        self._bm25 = None
        self._documents: List[str] = []
        self._element_ids: List[str] = []
        self._element_data: Dict[str, Dict] = {}
        self._tokenized_corpus: List[List[str]] = []

    def build(self, elements: List[CodeElement]):
        """Build the BM25 index from code elements.

        Args:
            elements: Code elements to index.
        """
        from rank_bm25 import BM25Okapi

        self._documents = []
        self._element_ids = []
        self._element_data = {}
        self._tokenized_corpus = []

        for el in elements:
            # Build searchable text
            text = el.to_search_text()
            self._documents.append(text)
            self._element_ids.append(el.element_id)
            self._element_data[el.element_id] = el.to_display_dict()

            # Tokenize for BM25
            tokens = _tokenize(text)
            self._tokenized_corpus.append(tokens)

        self._bm25 = BM25Okapi(self._tokenized_corpus)
        logger.info(f"Built BM25 index with {len(elements)} documents")

    def search(
        self,
        query: str,
        top_k: int = 50,
    ) -> List[Dict]:
        """Search the BM25 index.

        Args:
            query: Search query string.
            top_k: Number of results to return.

        Returns:
            List of results with scores and metadata.
        """
        if self._bm25 is None or not self._element_ids:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        # Get top-k indices sorted by score
        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True,
        )[:top_k]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score <= 0:
                continue

            element_id = self._element_ids[idx]
            data = self._element_data.get(element_id, {})

            results.append({
                "element_id": element_id,
                "bm25_score": score,
                **data,
            })

        return results

    def get_scores(self, query: str) -> List[Tuple[str, float]]:
        """Get BM25 scores for all documents.

        Returns:
            List of (element_id, score) tuples.
        """
        if self._bm25 is None:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)
        return [
            (self._element_ids[i], float(scores[i]))
            for i in range(len(scores))
        ]

    def save(self, path: str | Path):
        """Persist the BM25 index to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "documents": self._documents,
            "element_ids": self._element_ids,
            "element_data": self._element_data,
            "tokenized_corpus": self._tokenized_corpus,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info(f"Saved BM25 index to {path}")

    def load(self, path: str | Path) -> bool:
        """Load a BM25 index from disk.

        Returns:
            True if loading succeeded.
        """
        path = Path(path)
        if not path.exists():
            return False

        try:
            from rank_bm25 import BM25Okapi

            with open(path, "rb") as f:
                data = pickle.load(f)

            self._documents = data["documents"]
            self._element_ids = data["element_ids"]
            self._element_data = data["element_data"]
            self._tokenized_corpus = data["tokenized_corpus"]
            self._bm25 = BM25Okapi(self._tokenized_corpus)

            logger.info(f"Loaded BM25 index from {path} ({len(self._element_ids)} docs)")
            return True
        except Exception as e:
            logger.error(f"Failed to load BM25 index: {e}")
            return False

    @property
    def size(self) -> int:
        """Number of documents in the index."""
        return len(self._element_ids)

    def update_file(
        self,
        repo_name: str,
        file_path: str,
        new_elements: List[CodeElement],
    ) -> None:
        """Replace all entries for one file with `new_elements` and rebuild.

        Drops every existing document whose stored metadata matches
        (repo_name, file_path), appends the new elements, and re-fits BM25.
        """
        from rank_bm25 import BM25Okapi

        keep_docs: List[str] = []
        keep_ids: List[str] = []
        keep_tokens: List[List[str]] = []
        keep_data: Dict[str, Dict] = {}

        for i, eid in enumerate(self._element_ids):
            data = self._element_data.get(eid, {})
            if data.get("repo_name") == repo_name and data.get("file_path") == file_path:
                continue
            keep_docs.append(self._documents[i])
            keep_ids.append(eid)
            keep_tokens.append(self._tokenized_corpus[i])
            keep_data[eid] = data

        for el in new_elements:
            text = el.to_search_text()
            keep_docs.append(text)
            keep_ids.append(el.element_id)
            keep_tokens.append(_tokenize(text))
            keep_data[el.element_id] = el.to_display_dict()

        self._documents = keep_docs
        self._element_ids = keep_ids
        self._tokenized_corpus = keep_tokens
        self._element_data = keep_data
        self._bm25 = BM25Okapi(keep_tokens) if keep_tokens else None
        logger.info(
            f"BM25 updated for {repo_name}:{file_path} "
            f"(+{len(new_elements)} elements, total={len(keep_ids)})"
        )
