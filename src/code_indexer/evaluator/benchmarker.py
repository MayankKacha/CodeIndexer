"""
Benchmarker module for comparing different retrieval architectures.

Simulates three pipelines:
1. Baseline (Vector only, no rerank, no compression)
2. CodeGraphContext (Graph+Vector, no rerank, no compression)
3. CodeIndexer (Full Hybrid + Rerank + Compression)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List

from code_indexer.evaluator.metrics import RelevanceEvaluator, count_tokens
from code_indexer.pipeline.indexer import CodeIndexerPipeline

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Stores the metrics for a single search architecture."""
    architecture: str
    latency_ms: float
    token_count: int
    relevance_score: float
    context: str
    results: List[Dict[str, Any]]


class Benchmarker:
    """Runs a query across multiple retrieval pipelines to compare performance."""

    def __init__(self, pipeline: CodeIndexerPipeline):
        self.pipeline = pipeline
        self.evaluator = RelevanceEvaluator()

    async def run_benchmark(self, query: str, repo_name: str = "") -> Dict[str, BenchmarkResult]:
        """
        Run the query through all retrieval architectures in parallel and collect metrics.
        """
        logger.info(f"Running benchmark for query: '{query}'")
        
        # Define tasks for parallel execution
        tasks = [
            # 1. Baseline (Naive RAG) - Vector only
            self._evaluate_pipeline(
                name="Baseline",
                query=query,
                repo_name=repo_name,
                params={
                    "use_bm25": False,
                    "use_vector": True,
                    "use_graph": False,
                    "rerank": False,
                },
                compress=False,
                top_k=5,
            ),
            # 2. Simulated CodeGraphContext (Graph + Vector)
            self._evaluate_pipeline(
                name="CodeGraphContext (Simulated)",
                query=query,
                repo_name=repo_name,
                params={
                    "use_bm25": True,
                    "use_vector": True,
                    "use_graph": True,
                    "rerank": False,
                },
                compress=False,
                top_k=5,
            ),
            # 3. Real CodeGraphContext (CGC Engine)
            self._evaluate_cgc(
                query=query,
                repo_name=repo_name,
                top_k=5
            ),
            # 4. CodeIndexer (Our Version: Hybrid + Rerank + Compress)
            self._evaluate_pipeline(
                name="CodeIndexer",
                query=query,
                repo_name=repo_name,
                params={
                    "use_bm25": True,
                    "use_vector": True,
                    "use_graph": True,
                    "rerank": True,
                },
                compress=True,
                top_k=10,
            )
        ]
        
        # Run all evaluations in parallel
        benchmark_list = await asyncio.gather(*tasks)
        
        # Convert list back to result dictionary
        results = {res.architecture: res for res in benchmark_list}
        return results

    async def _evaluate_cgc(
        self,
        query: str,
        repo_name: str,
        top_k: int
    ) -> BenchmarkResult:
        """Evaluates the real CodeGraphContext engine (Async wrapper)."""
        return await asyncio.to_thread(self._evaluate_cgc_sync, query, repo_name, top_k)

    def _evaluate_cgc_sync(
        self,
        query: str,
        repo_name: str,
        top_k: int
    ) -> BenchmarkResult:
        """Original synchronous logic for CGC evaluation."""
        from code_indexer.evaluator.cgc_connector import CGCConnector
        
        start_time = time.time()
        name = "CodeGraphContext (Real)"
        
        # Use current workspace as default repo path for CGC search
        repo_path = os.getcwd() 
        
        try:
            connector = CGCConnector(repo_path)
            context_str = connector.search_context(query, top_k=top_k)
        except Exception as e:
            logger.error(f"Failed to initialize real CGC: {e}")
            context_str = ""

        if not context_str.strip():
            logger.warning(f"{name}: Context is empty.")
            latency = (time.time() - start_time) * 1000
            return BenchmarkResult(
                architecture=name,
                latency_ms=latency,
                token_count=0,
                relevance_score=0.0,
                context="",
                results=[],
            )

        # Standard processing for tokens and relevance
        system_prompt = (
            "You are an expert software engineer and architect. "
            "Your task is to answer questions about a codebase using the provided context."
        )
        user_prompt = f"Context:\n{context_str}\n\nQuestion:\n{query}"
        total_prompt = system_prompt + "\n" + user_prompt
        
        tokens = count_tokens(total_prompt)
        evaluator_context = context_str[:40000]
        relevance = self.evaluator.evaluate_relevance(query, evaluator_context)
        
        latency = (time.time() - start_time) * 1000
        logger.info(f"{name} completed: {latency:.2f}ms | {tokens} tokens | Score: {relevance}/10")

        return BenchmarkResult(
            architecture=name,
            latency_ms=latency,
            token_count=tokens,
            relevance_score=relevance,
            context=context_str,
            results=[],  # We don't need raw results for display in benchmark
        )

    async def _evaluate_pipeline(
        self,
        name: str,
        query: str,
        repo_name: str,
        params: dict,
        compress: bool,
        top_k: int,
    ) -> BenchmarkResult:
        """Async wrapper for evaluate_pipeline."""
        return await asyncio.to_thread(
            self._evaluate_pipeline_sync, name, query, repo_name, params, compress, top_k
        )

    def _evaluate_pipeline_sync(
        self,
        name: str,
        query: str,
        repo_name: str,
        params: dict,
        compress: bool,
        top_k: int,
    ) -> BenchmarkResult:
        """Original synchronous helper to run a specific pipeline configuration."""
        start_time = time.time()
        
        # Override the hybrid_search defaults temporarily
        # Fast hack: call the underlying search engine directly for raw results
        engine = self.pipeline.hybrid_search
        
        candidates = engine.search(
            query=query,
            top_k=top_k,
            repo_name=repo_name,
            **params
        )

        # Handle Re-ranking if demanded
        if params.get("rerank", False) and len(candidates) > 1:
            try:
                candidates = self.pipeline.reranker.rerank(
                    query=query,
                    results=candidates,
                    top_k=top_k,
                )
            except Exception as e:
                logger.warning(f"Re-ranking failed in benchmark: {e}")

        # Assemble context string
        context_str = ""
        if compress and self.pipeline.compressor and candidates:
            try:
                # Force fresh compression in benchmarks — never use cached result
                # as the cached context may relate to a different query variant
                compression_data = self.pipeline.compressor.compress(query, candidates)
                context_str = compression_data.get("compressed_context", "")
                if not context_str:
                    logger.warning(f"{name}: Compression returned empty context. Falling back to naive concat.")
            except Exception as e:
                logger.warning(f"Compression failed in benchmark '{name}': {e}")

        if not context_str:
            # Standard naive concatenation for baseline / fallback
            # Limit to top_k to avoid blowing up token counts for baselines
            limited_candidates = candidates[:top_k]
            context_str = "\n\n".join([
                f"File: {r.get('file_path', 'unknown')} | Component: {r.get('qualified_name', r.get('name', 'unknown'))}\n"
                f"Lines {r.get('start_line', '?')}-{r.get('end_line', '?')}\n"
                f"```python\n{r.get('code', '')}\n```"
                for r in limited_candidates
                if r.get('code', '').strip()  # skip results with no code text
            ])

        if not context_str.strip():
            logger.error(f"{name}: Context is EMPTY after assembly. candidates={len(candidates)}, first_candidate={candidates[0] if candidates else 'none'}")
        else:
            logger.debug(f"{name}: context snippet = {context_str[:300]!r}")

        # Calculate actual exact RAG agent token consumption
        system_prompt = (
            "You are an expert software engineer and architect. "
            "Your task is to answer questions about a codebase using the provided context. "
            "The context contains highly compressed snippets from the codebase that match the user's query. "
            "Always cite the files and methods you reference. "
            "If the context does not contain enough information to answer the question securely, say so. "
            "Keep your explanations clear, concise, and focused on the code."
        )
        user_prompt = f"Context:\n{context_str}\n\nQuestion:\n{query}"
        total_prompt = system_prompt + "\n" + user_prompt
        
        tokens = count_tokens(total_prompt)
        
        # Protect evaluator token limits (truncate extremely massive baseline contexts for evaluator)
        # We slice raw characters to ~40k max (roughly 10k tokens) to prevent rate limit exceptions
        # which were causing the relevance score 0.0 bug for uncompressed baselines.
        evaluator_context = context_str[:40000]
            
        relevance = self.evaluator.evaluate_relevance(query, evaluator_context)
        
        latency = (time.time() - start_time) * 1000  # ms
        
        logger.info(f"{name} completed: {latency:.2f}ms | {tokens} tokens | Score: {relevance}/10")

        return BenchmarkResult(
            architecture=name,
            latency_ms=latency,
            token_count=tokens,
            relevance_score=relevance,
            context=context_str,
            results=candidates,
        )
