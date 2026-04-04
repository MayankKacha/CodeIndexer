"""
Main indexing pipeline – orchestrates the full indexing workflow.

Flow:
1. Clone repo (if GitHub URL) or scan local directory
2. Parse files → extract code elements with tree-sitter
3. Generate descriptions via OpenAI (optional)
4. Encode elements via CodeBERT → embeddings
5. Store in Graph DB (nodes + relationships)
6. Store in Milvus (embeddings + metadata)
7. Build BM25 index
8. Report statistics

Supports incremental indexing: files that haven't changed
since the last index are automatically skipped.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from code_indexer.cache.cache_manager import CacheManager
from code_indexer.config.settings import Settings, get_settings
from code_indexer.parsing.code_splitter import split_codebase
from code_indexer.parsing.models import CodeElement, IndexingStats
from code_indexer.pipeline.git_cloner import clone_repository, extract_repo_name, is_github_url
from code_indexer.search.bm25_index import BM25Index
from code_indexer.vectors.encoder import CodeEncoder

logger = logging.getLogger(__name__)

# Type alias for progress callbacks
ProgressCallback = Optional[Callable[[str, str, Optional[dict]], None]]


class CodeIndexerPipeline:
    """Orchestrates the complete code indexing pipeline."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._neo4j_store = None
        self._milvus_store = None
        self._encoder = None
        self._bm25_index = None
        self._description_generator = None
        self._compressor = None
        self._reranker = None
        self._cache = None
        self._graph_queries = None
        self._hybrid_search = None
        self._rag_agent = None

    # ── Lazy Initialization ─────────────────────────────────────────────

    @property
    def graph_store(self):
        if self._neo4j_store is None:
            if self.settings.graph_backend.lower() == "neo4j":
                from code_indexer.graph.neo4j_store import Neo4jStore
                self._neo4j_store = Neo4jStore(
                    uri=self.settings.neo4j_uri,
                    username=self.settings.neo4j_username,
                    password=self.settings.neo4j_password,
                )
            else:
                from code_indexer.graph.networkx_store import NetworkxStore
                cache_path = Path(self.settings.cache_dir) / "graph.pkl"
                self._neo4j_store = NetworkxStore(persist_path=str(cache_path))
        return self._neo4j_store

    @property
    def milvus_store(self):
        if self._milvus_store is None:
            from code_indexer.vectors.milvus_store import MilvusStore
            self._milvus_store = MilvusStore(
                uri=self.settings.milvus_uri,
                token=self.settings.milvus_token,
                collection_name=self.settings.milvus_collection,
                embedding_dim=self.settings.embedding_dim,
            )
        return self._milvus_store

    @property
    def encoder(self):
        if self._encoder is None:
            device = self.settings.auto_detect_device()
            self._encoder = CodeEncoder(
                model_name=self.settings.encoder_model,
                device=device,
            )
        return self._encoder

    @property
    def bm25_index(self):
        if self._bm25_index is None:
            self._bm25_index = BM25Index()
            # Try loading from disk
            cache_path = Path(self.settings.cache_dir) / "bm25_index.pkl"
            self._bm25_index.load(cache_path)
        return self._bm25_index

    @property
    def description_generator(self):
        if self._description_generator is None and self.settings.openai_api_key:
            from code_indexer.enrichment.description_generator import DescriptionGenerator
            self._description_generator = DescriptionGenerator(
                api_key=self.settings.openai_api_key,
                model=self.settings.openai_model,
            )
        return self._description_generator

    @property
    def compressor(self):
        if self._compressor is None and self.settings.openai_api_key:
            from code_indexer.compression.compressor import QueryCompressor
            self._compressor = QueryCompressor(
                api_key=self.settings.openai_api_key,
                model=self.settings.openai_model,
                strategy=self.settings.compression_strategy,
                max_tokens=self.settings.max_compressed_tokens,
            )
        return self._compressor

    @property
    def reranker(self):
        if self._reranker is None:
            from code_indexer.search.reranker import Reranker
            device = self.settings.auto_detect_device()
            self._reranker = Reranker(device=device)
        return self._reranker

    @property
    def cache(self):
        if self._cache is None:
            self._cache = CacheManager(
                cache_dir=self.settings.cache_dir,
                ttl=self.settings.cache_ttl,
            )
        return self._cache

    @property
    def graph_queries(self):
        if self._graph_queries is None:
            if self.settings.graph_backend.lower() == "neo4j":
                from code_indexer.graph.graph_queries import GraphQueries
                self._graph_queries = GraphQueries(self.graph_store.driver)
            else:
                from code_indexer.graph.graph_queries_networkx import GraphQueriesNetworkx
                self._graph_queries = GraphQueriesNetworkx(self.graph_store)
        return self._graph_queries

    @property
    def hybrid_search(self):
        if self._hybrid_search is None:
            from code_indexer.search.hybrid_search import HybridSearchEngine
            self._hybrid_search = HybridSearchEngine(
                bm25_index=self.bm25_index,
                milvus_store=self.milvus_store,
                encoder=self.encoder,
                graph_queries=self.graph_queries,
                bm25_weight=self.settings.bm25_weight,
                vector_weight=self.settings.vector_weight,
            )
        return self._hybrid_search

    @property
    def rag_agent(self):
        if self._rag_agent is None and self.settings.openai_api_key:
            from code_indexer.rag.agent import CodeAssistant
            self._rag_agent = CodeAssistant(
                pipeline=self,
                api_key=self.settings.openai_api_key,
                model=self.settings.openai_chat_model,
            )
        return self._rag_agent

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _file_content_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of a file's contents."""
        h = hashlib.sha256()
        try:
            h.update(file_path.read_bytes())
        except Exception:
            return ""
        return h.hexdigest()

    def _emit(self, callback: ProgressCallback, step: str, message: str, data: dict = None):
        """Emit a progress event if a callback is provided."""
        if callback:
            callback(step, message, data)

    # ── Indexing ────────────────────────────────────────────────────────

    def index(
        self,
        path: str,
        repo_name: str = "",
        generate_descriptions: bool = True,
        use_neo4j: bool = True,
        use_milvus: bool = True,
        progress_callback: ProgressCallback = None,
    ) -> IndexingStats:
        """Index a codebase (local directory or GitHub URL).

        Args:
            path: Local directory path or GitHub URL.
            repo_name: Optional repository name override.
            generate_descriptions: Whether to generate LLM descriptions.
            use_neo4j: Whether to store in the graph database.
            use_milvus: Whether to store in Milvus.
            progress_callback: Optional callback(step, message, data) for
                real-time progress updates (used by SSE endpoint).

        Returns:
            IndexingStats with counts and timing.
        """
        start_time = time.time()
        stats = IndexingStats()

        # ── Step 1: Resolve path ────────────────────────────────────────
        self._emit(progress_callback, "clone", "Resolving repository path...")
        if is_github_url(path):
            logger.info(f"Cloning repository: {path}")
            if not repo_name:
                repo_name = extract_repo_name(path)
            self._emit(progress_callback, "clone", f"Cloning {repo_name} from GitHub...")
            local_path = clone_repository(
                url=path,
                clone_dir=self.settings.clone_dir,
            )
            self._emit(progress_callback, "clone", f"Clone complete: {local_path}", {"repo_name": repo_name})
        else:
            local_path = Path(path).resolve()
            if not local_path.is_dir():
                raise ValueError(f"Not a directory: {local_path}")
            if not repo_name:
                repo_name = local_path.name

        stats.repo_name = repo_name
        stats.local_repo_path = str(local_path)
        logger.info(f"Indexing repository: {repo_name} at {local_path}")

        # ── Step 2: Parse code ──────────────────────────────────────────
        self._emit(progress_callback, "parse", "Parsing code with tree-sitter...")
        parse_start = time.time()
        elements, parse_stats = split_codebase(local_path, repo_name=repo_name)
        stats.parse_time_seconds = round(time.time() - parse_start, 2)

        if not elements:
            logger.warning("No code elements found!")
            self._emit(progress_callback, "parse", "No code elements found.", {"count": 0})
            stats.indexing_time_seconds = time.time() - start_time
            return stats

        stats.total_elements = len(elements)
        stats.functions = parse_stats.get("functions", 0)
        stats.methods = parse_stats.get("methods", 0)
        stats.classes = parse_stats.get("classes", 0)
        stats.total_lines = parse_stats.get("total_lines", 0)
        stats.languages = parse_stats.get("languages", {})
        stats.total_files = parse_stats.get("total_files", 0)
        stats.parsed_files = parse_stats.get("parsed_files", 0)

        self._emit(progress_callback, "parse",
                   f"Parsed {stats.total_elements} elements ({stats.functions} functions, "
                   f"{stats.methods} methods, {stats.classes} classes)",
                   {"total_elements": stats.total_elements, "languages": stats.languages})

        logger.info(
            f"Parsed {stats.total_elements} elements: "
            f"{stats.functions} functions, {stats.methods} methods, "
            f"{stats.classes} classes"
        )

        # ── Step 2b: Incremental check — skip unchanged files ───────────
        existing_meta = self.cache.get_repo_metadata(repo_name)
        if existing_meta:
            stats.is_incremental = True
            changed_elements = []
            unchanged_files = set()

            for el in elements:
                fp = Path(local_path) / el.file_path if not Path(el.file_path).is_absolute() else Path(el.file_path)
                if fp.exists():
                    current_hash = self._file_content_hash(fp)
                    cached_hash = self.cache.get_file_hash(repo_name, str(el.file_path))
                    if cached_hash == current_hash:
                        unchanged_files.add(str(el.file_path))
                    else:
                        changed_elements.append(el)
                        self.cache.set_file_hash(repo_name, str(el.file_path), current_hash)
                else:
                    changed_elements.append(el)

            stats.files_unchanged = len(unchanged_files)
            stats.files_changed = len(set(el.file_path for el in changed_elements))

            if not changed_elements:
                self._emit(progress_callback, "skip",
                           f"All {len(unchanged_files)} files unchanged — skipping re-index.",
                           {"files_unchanged": stats.files_unchanged})
                logger.info("All files unchanged, skipping re-index.")
                stats.indexing_time_seconds = round(time.time() - start_time, 2)
                return stats

            self._emit(progress_callback, "incremental",
                       f"{stats.files_changed} files changed, {stats.files_unchanged} unchanged — indexing changes only.",
                       {"files_changed": stats.files_changed, "files_unchanged": stats.files_unchanged})

            elements = changed_elements
        else:
            # First-time index: store all file hashes
            for el in elements:
                fp = Path(local_path) / el.file_path if not Path(el.file_path).is_absolute() else Path(el.file_path)
                if fp.exists():
                    self.cache.set_file_hash(repo_name, str(el.file_path), self._file_content_hash(fp))

        # ── Step 3: Generate descriptions ───────────────────────────────
        desc_start = time.time()
        if generate_descriptions and self.description_generator:
            self._emit(progress_callback, "descriptions", "Generating LLM descriptions...")
            logger.info("Step 2/5: Generating LLM descriptions...")
            try:
                elements = self.description_generator.generate_descriptions_batch(elements)
            except Exception as e:
                logger.warning(f"Description generation failed: {e}")
        else:
            self._emit(progress_callback, "descriptions", "Skipping description generation.")
            logger.info("Step 2/5: Skipping description generation (no API key)")
        stats.description_time_seconds = round(time.time() - desc_start, 2)

        # ── Step 4: Generate embeddings ─────────────────────────────────
        self._emit(progress_callback, "embeddings", f"Generating embeddings for {len(elements)} elements...")
        logger.info("Step 3/5: Generating CodeBERT embeddings...")
        embed_start = time.time()
        embedding_texts = [el.to_embedding_text() for el in elements]

        # Use cache for embeddings
        cached_embeddings = self.cache.get_embeddings_batch(embedding_texts)
        texts_to_encode = []
        encode_indices = []

        for i, text in enumerate(embedding_texts):
            if i not in cached_embeddings:
                texts_to_encode.append(text)
                encode_indices.append(i)

        if texts_to_encode:
            new_embeddings = self.encoder.encode_batch(texts_to_encode)
            self.cache.set_embeddings_batch(texts_to_encode, new_embeddings)

            # Merge cached and new embeddings
            all_embeddings = [None] * len(elements)
            for i, emb in cached_embeddings.items():
                all_embeddings[i] = emb
            for j, idx in enumerate(encode_indices):
                all_embeddings[idx] = new_embeddings[j]
        else:
            all_embeddings = [cached_embeddings[i] for i in range(len(elements))]

        stats.embedding_count = len(all_embeddings)
        stats.embedding_time_seconds = round(time.time() - embed_start, 2)

        self._emit(progress_callback, "embeddings",
                   f"Generated {len(texts_to_encode)} new embeddings ({len(cached_embeddings)} cached).",
                   {"new": len(texts_to_encode), "cached": len(cached_embeddings)})
        logger.info(
            f"Generated {len(texts_to_encode)} new embeddings "
            f"({len(cached_embeddings)} from cache)"
        )

        # ── Step 5a: Store in Graph DB ──────────────────────────────────
        graph_start = time.time()
        if use_neo4j:
            self._emit(progress_callback, "graph", f"Storing in {self.settings.graph_backend} graph database...")
            logger.info(f"Step 4/5: Storing in {self.settings.graph_backend} graph database...")
            try:
                if not stats.is_incremental:
                    self.graph_store.clear_repository(repo_name)
                graph_result = self.graph_store.store_elements(elements)
                stats.graph_nodes = graph_result.get("nodes", 0)
                stats.graph_relationships = graph_result.get("relationships", 0)
                self._emit(progress_callback, "graph",
                           f"Stored {stats.graph_nodes} nodes, {stats.graph_relationships} relationships.",
                           {"nodes": stats.graph_nodes, "relationships": stats.graph_relationships})
                logger.info(
                    f"Stored {stats.graph_nodes} nodes, "
                    f"{stats.graph_relationships} relationships in Graph"
                )
            except Exception as e:
                logger.error(f"Graph storage failed: {e}")
                stats.errors.append(f"Graph: {e}")
        stats.graph_time_seconds = round(time.time() - graph_start, 2)

        # ── Step 5b: Store in Milvus ────────────────────────────────────
        vector_start = time.time()
        if use_milvus:
            self._emit(progress_callback, "vectors", "Storing vectors in Milvus...")
            logger.info("Step 5/5: Storing vectors in Milvus...")
            try:
                if not stats.is_incremental:
                    self.milvus_store.delete_by_repo(repo_name)
                inserted = self.milvus_store.insert_elements(elements, all_embeddings)
                self._emit(progress_callback, "vectors", f"Inserted {inserted} elements into Milvus.",
                           {"inserted": inserted})
                logger.info(f"Inserted {inserted} elements into Milvus")
            except Exception as e:
                logger.error(f"Milvus storage failed: {e}")
                stats.errors.append(f"Milvus: {e}")
        stats.vector_time_seconds = round(time.time() - vector_start, 2)

        # ── Step 6: Build BM25 index ────────────────────────────────────
        bm25_start = time.time()
        self._emit(progress_callback, "bm25", "Building BM25 search index...")
        logger.info("Building BM25 search index...")
        self.bm25_index.build(elements)
        bm25_path = Path(self.settings.cache_dir) / "bm25_index.pkl"
        self.bm25_index.save(bm25_path)
        stats.bm25_time_seconds = round(time.time() - bm25_start, 2)

        # ── Invalidate caches ───────────────────────────────────────────
        self.cache.invalidate_repo(repo_name)

        # ── Final stats ─────────────────────────────────────────────────
        stats.indexing_time_seconds = round(time.time() - start_time, 2)

        # Store repo metadata for analytics dashboard
        self.cache.set_repo_metadata(repo_name, {
            "repo_name": repo_name,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats.to_dict(),
        })

        self._emit(progress_callback, "complete",
                   f"Indexing complete in {stats.indexing_time_seconds}s",
                   stats.to_dict())

        logger.info(
            f"✅ Indexing complete in {stats.indexing_time_seconds}s: "
            f"{stats.total_elements} elements, "
            f"{stats.embedding_count} embeddings"
        )

        return stats

    # ── Search ──────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 0,
        repo_name: str = "",
        use_reranker: bool = True,
        use_compression: bool = False,
    ) -> Dict[str, Any]:
        """Search the indexed codebase.

        Args:
            query: Search query.
            top_k: Number of results (0 = use default from settings).
            repo_name: Optional repository filter.
            use_reranker: Whether to apply cross-encoder re-ranking.
            use_compression: Whether to apply query-aware compression.

        Returns:
            Dict with results, compression stats, and metadata.
        """
        if top_k <= 0:
            top_k = self.settings.final_top_k

        # Check cache
        cached = self.cache.get_search_results(query, repo_name)
        if cached:
            return {"results": cached, "from_cache": True}

        # Hybrid search
        candidates = self.hybrid_search.search(
            query=query,
            top_k=top_k,
            repo_name=repo_name,
            rerank=use_reranker,
        )

        # Re-rank
        if use_reranker and len(candidates) > 1:
            try:
                results = self.reranker.rerank(
                    query=query,
                    results=candidates,
                    top_k=top_k,
                )
            except Exception as e:
                logger.warning(f"Re-ranking failed, using unranked results: {e}")
                results = candidates[:top_k]
        else:
            results = candidates[:top_k]

        # Cache results
        self.cache.set_search_results(query, results, repo_name)

        response: Dict[str, Any] = {
            "results": results,
            "from_cache": False,
            "total_candidates": len(candidates),
        }

        # Optional compression
        if use_compression and self.compressor and results:
            result_ids = [r.get("element_id", r.get("id", "")) for r in results]
            cached_compressed = self.cache.get_compressed(query, result_ids)

            if cached_compressed:
                response["compression"] = cached_compressed
            else:
                # Estimate total tokens before compression — only compress if high volume
                total_context = self.compressor._build_original_context(results)
                estimated_tokens = self.compressor._estimate_tokens(total_context)

                if estimated_tokens > 2000:
                    compressed = self.compressor.compress(query, results)
                    self.cache.set_compressed(query, result_ids, compressed)
                    response["compression"] = compressed
                    response["compression_skipped"] = False
                else:
                    # Low token count, skip slow LLM compression
                    response["compression"] = {
                        "compressed_context": total_context,
                        "original_tokens": estimated_tokens,
                        "compressed_tokens": estimated_tokens,
                        "compression_ratio": 0.0,
                        "elements": [
                             {
                                "name": r.get("name", ""),
                                "qualified_name": r.get("qualified_name", ""),
                                "file_path": r.get("file_path", ""),
                                "start_line": r.get("start_line", 0),
                                "end_line": r.get("end_line", 0),
                                "element_type": r.get("element_type", ""),
                            }
                            for r in results
                        ]
                    }
                    response["compression_skipped"] = True

        return response

    # ── Graph Queries ───────────────────────────────────────────────────

    def find_callers(self, name: str, repo_name: str = "") -> List[Dict]:
        """Find all callers of a function/method."""
        return self.graph_queries.find_callers(name, repo_name)

    def find_callees(self, name: str, repo_name: str = "") -> List[Dict]:
        """Find all functions called by a given function."""
        return self.graph_queries.find_callees(name, repo_name)

    def find_call_chain(self, from_name: str, to_name: str) -> List[Dict]:
        """Find call chain between two functions."""
        return self.graph_queries.find_call_chain(from_name, to_name)

    def impact_analysis(self, name: str) -> Dict:
        """Analyze impact of changing a function."""
        return self.graph_queries.impact_analysis(name)

    def find_dead_code(self, repo_name: str = "") -> List[Dict]:
        """Find potentially dead code."""
        return self.graph_queries.find_dead_code(repo_name)

    def list_repositories(self) -> List[Dict]:
        """List all indexed repositories."""
        return self.graph_store.list_repositories()

    def delete_repository(self, repo_name: str):
        """Delete a repository from all stores."""
        self.graph_store.delete_repository(repo_name)
        self.milvus_store.delete_by_repo(repo_name)
        self.cache.invalidate_repo(repo_name)
        logger.info(f"Deleted repository: {repo_name}")

    def get_stats(self) -> Dict:
        """Get system statistics."""
        graph_stats = self.graph_queries.get_stats()
        cache_stats = self.cache.get_stats()
        return {
            "graph": graph_stats,
            "cache": cache_stats,
            "bm25_index_size": self.bm25_index.size,
        }

    def close(self):
        """Clean up resources."""
        if self._neo4j_store:
            self._neo4j_store.close()
        if self._cache:
            self._cache.close()
