"""
Cache manager using diskcache for persistent, disk-backed caching.

Caches search results, embeddings, and compressed outputs to avoid
redundant computation and API calls.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CacheManager:
    """Disk-backed cache for search results, embeddings, and compressions."""

    def __init__(self, cache_dir: str = "./.codeindexer_cache", ttl: int = 3600):
        """Initialize the cache.

        Args:
            cache_dir: Directory for cache storage.
            ttl: Default time-to-live in seconds.
        """
        import diskcache

        self.cache_dir = Path(cache_dir)
        self.ttl = ttl

        # Separate caches for different data types
        self.search_cache = diskcache.Cache(str(self.cache_dir / "search"))
        self.embedding_cache = diskcache.Cache(str(self.cache_dir / "embeddings"))
        self.compression_cache = diskcache.Cache(str(self.cache_dir / "compression"))
        self.file_hash_cache = diskcache.Cache(str(self.cache_dir / "file_hashes"))
        self.repo_metadata_cache = diskcache.Cache(str(self.cache_dir / "repo_metadata"))

        logger.info(f"Cache initialized at {cache_dir}")

    @staticmethod
    def _hash_key(key: str) -> str:
        """Create a hash key for cache lookups."""
        return hashlib.sha256(key.encode()).hexdigest()

    # ── Search Cache ────────────────────────────────────────────────────

    def get_search_results(self, query: str, repo_name: str = "") -> Optional[List[Dict]]:
        """Retrieve cached search results."""
        key = self._hash_key(f"search:{query}:{repo_name}")
        result = self.search_cache.get(key)
        if result is not None:
            logger.debug(f"Search cache HIT for: '{query}'")
        return result

    def set_search_results(
        self, query: str, results: List[Dict], repo_name: str = ""
    ):
        """Cache search results."""
        key = self._hash_key(f"search:{query}:{repo_name}")
        self.search_cache.set(key, results, expire=self.ttl)
        logger.debug(f"Search cache SET for: '{query}' ({len(results)} results)")

    # ── Embedding Cache ─────────────────────────────────────────────────

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Retrieve a cached embedding."""
        key = self._hash_key(f"embed:{text}")
        return self.embedding_cache.get(key)

    def set_embedding(self, text: str, embedding: List[float]):
        """Cache an embedding."""
        key = self._hash_key(f"embed:{text}")
        # Embeddings don't expire (code doesn't change often)
        self.embedding_cache.set(key, embedding, expire=self.ttl * 24)

    def get_embeddings_batch(self, texts: List[str]) -> Dict[int, List[float]]:
        """Get cached embeddings for multiple texts.

        Returns:
            Dict mapping text index to cached embedding (only for cache hits).
        """
        cached = {}
        for i, text in enumerate(texts):
            emb = self.get_embedding(text)
            if emb is not None:
                cached[i] = emb
        return cached

    def set_embeddings_batch(self, texts: List[str], embeddings: List[List[float]]):
        """Cache multiple embeddings."""
        for text, emb in zip(texts, embeddings):
            self.set_embedding(text, emb)

    # ── Compression Cache ───────────────────────────────────────────────

    def get_compressed(
        self, query: str, result_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Retrieve cached compressed context."""
        key = self._hash_key(f"compress:{query}:{':'.join(sorted(result_ids))}")
        return self.compression_cache.get(key)

    def set_compressed(
        self, query: str, result_ids: List[str], compressed: Dict[str, Any]
    ):
        """Cache compressed context."""
        key = self._hash_key(f"compress:{query}:{':'.join(sorted(result_ids))}")
        self.compression_cache.set(key, compressed, expire=self.ttl)

    # ── File Hash Cache (Incremental Indexing) ──────────────────────────

    def get_file_hash(self, repo_name: str, file_path: str) -> Optional[str]:
        """Get the stored content hash for a file."""
        key = self._hash_key(f"filehash:{repo_name}:{file_path}")
        return self.file_hash_cache.get(key)

    def set_file_hash(self, repo_name: str, file_path: str, content_hash: str):
        """Store the content hash for a file (no expiration)."""
        key = self._hash_key(f"filehash:{repo_name}:{file_path}")
        self.file_hash_cache.set(key, content_hash)

    def clear_file_hashes(self, repo_name: str):
        """Clear all file hashes for a repository."""
        self.file_hash_cache.clear()

    # ── Repository Metadata Cache (Analytics) ───────────────────────────

    def get_repo_metadata(self, repo_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored indexing metadata for a repository."""
        key = self._hash_key(f"repometa:{repo_name}")
        return self.repo_metadata_cache.get(key)

    def set_repo_metadata(self, repo_name: str, metadata: Dict[str, Any]):
        """Store indexing metadata for a repository (no expiration)."""
        key = self._hash_key(f"repometa:{repo_name}")
        self.repo_metadata_cache.set(key, metadata)

    def get_all_repo_metadata(self) -> List[Dict[str, Any]]:
        """Return metadata for all indexed repositories."""
        results = []
        for key in self.repo_metadata_cache:
            val = self.repo_metadata_cache.get(key)
            if val and isinstance(val, dict):
                results.append(val)
        return results

    # ── Management ──────────────────────────────────────────────────────

    def invalidate_repo(self, repo_name: str):
        """Invalidate all caches related to a repository.

        Note: This is a best-effort clear since we can't efficiently
        filter diskcache by content. Clears all search and compression caches.
        """
        self.search_cache.clear()
        self.compression_cache.clear()
        logger.info(f"Invalidated caches for repository: {repo_name}")

    def clear_all(self):
        """Clear all caches."""
        self.search_cache.clear()
        self.embedding_cache.clear()
        self.compression_cache.clear()
        self.file_hash_cache.clear()
        self.repo_metadata_cache.clear()
        logger.info("All caches cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        return {
            "search_cache_size": len(self.search_cache),
            "embedding_cache_size": len(self.embedding_cache),
            "compression_cache_size": len(self.compression_cache),
            "file_hash_cache_size": len(self.file_hash_cache),
            "search_cache_volume": self.search_cache.volume(),
            "embedding_cache_volume": self.embedding_cache.volume(),
            "compression_cache_volume": self.compression_cache.volume(),
            "cache_dir": str(self.cache_dir),
        }

    def close(self):
        """Close all cache connections."""
        self.search_cache.close()
        self.embedding_cache.close()
        self.compression_cache.close()
        self.file_hash_cache.close()
        self.repo_metadata_cache.close()

