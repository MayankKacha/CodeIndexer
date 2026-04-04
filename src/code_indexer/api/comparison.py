"""
Comparison API — side-by-side retrieval from CodeIndexer and CodeGraphContext.

Returns raw documents, retrieval time, and token counts for each system
so the UI can display a head-to-head comparison.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Comparison"])


# ── Request / Response Models ─────────────────────────────────────────

class CompareRequest(BaseModel):
    query: str = Field(..., description="Search query to compare across systems")
    repo_name: str = Field(default="", description="Repository name (for CodeIndexer)")
    top_k: int = Field(default=10, description="Number of results to retrieve")


# ── Token counting ────────────────────────────────────────────────────

def _count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count tokens using tiktoken."""
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # Rough fallback: 1 token ≈ 4 chars
        return len(text) // 4


_SYSTEM_PROMPT = (
    "You are an expert software engineer and architect. "
    "Your task is to answer questions about a codebase using the provided context. "
    "The context contains code snippets from the codebase that match the user's query. "
    "Always cite the files and methods you reference. "
    "If the context does not contain enough information to answer the question, say so. "
    "Keep your explanations clear, concise, and focused on the code."
)


def _tokens_for_context(query: str, context: str) -> Dict[str, int]:
    """Calculate token breakdown for a full RAG prompt."""
    system_tokens = _count_tokens(_SYSTEM_PROMPT)
    context_tokens = _count_tokens(context)
    query_tokens = _count_tokens(f"Question:\n{query}")
    return {
        "system_prompt_tokens": system_tokens,
        "context_tokens": context_tokens,
        "query_tokens": query_tokens,
        "total_tokens": system_tokens + context_tokens + query_tokens,
    }


# ── CodeIndexer retrieval ─────────────────────────────────────────────

def _run_codeindexer(pipeline, query: str, repo_name: str, top_k: int) -> Dict[str, Any]:
    """Run CodeIndexer's full retrieval pipeline and return results + metrics."""
    start = time.time()

    # Step 1: Hybrid search
    candidates = pipeline.hybrid_search.search(
        query=query,
        top_k=top_k,
        repo_name=repo_name,
        rerank=True,
    )

    # Step 2: Rerank
    if len(candidates) > 1:
        try:
            results = pipeline.reranker.rerank(
                query=query,
                results=candidates,
                top_k=top_k,
            )
        except Exception as e:
            logger.warning(f"Re-ranking failed: {e}")
            results = candidates[:top_k]
    else:
        results = candidates[:top_k]

    retrieval_time = (time.time() - start) * 1000  # ms

    # Build context string (what would be sent to LLM)
    context_parts = []
    formatted_results = []
    for r in results:
        name = r.get("qualified_name", r.get("name", "unknown"))
        file_path = r.get("file_path", "unknown")
        start_line = r.get("start_line", "?")
        end_line = r.get("end_line", "?")
        code = r.get("code", "")
        element_type = r.get("element_type", "")
        description = r.get("description", "")
        language = r.get("language", "python")
        signature = r.get("signature", "")
        rrf_score = r.get("rrf_score", 0)
        rerank_score = r.get("rerank_score", 0)

        context_parts.append(
            f"File: {file_path} | Component: {name}\n"
            f"Lines {start_line}-{end_line}\n"
            f"```{language}\n{code}\n```"
        )

        formatted_results.append({
            "name": name,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "code": code,
            "element_type": element_type,
            "description": description,
            "language": language,
            "signature": signature,
            "rrf_score": round(rrf_score, 4) if rrf_score else 0,
            "rerank_score": round(rerank_score, 4) if rerank_score else 0,
        })

    context_str = "\n\n".join(context_parts)

    # Also compute compressed context token count
    compressed_context_str = context_str
    compression_info = None
    if pipeline.compressor and results:
        try:
            comp_start = time.time()
            compression_data = pipeline.compressor.compress(query, results)
            comp_time = (time.time() - comp_start) * 1000
            compressed_text = compression_data.get("compressed_context", "")
            if compressed_text:
                compressed_context_str = compressed_text
                compression_info = {
                    "original_tokens": compression_data.get("original_tokens", 0),
                    "compressed_tokens": compression_data.get("compressed_tokens", 0),
                    "compression_ratio": compression_data.get("compression_ratio", 0),
                    "compression_time_ms": round(comp_time, 1),
                }
        except Exception as e:
            logger.warning(f"Compression failed in comparison: {e}")

    token_breakdown = _tokens_for_context(query, context_str)
    compressed_token_breakdown = _tokens_for_context(query, compressed_context_str)

    return {
        "results": formatted_results,
        "result_count": len(formatted_results),
        "retrieval_time_ms": round(retrieval_time, 1),
        "token_breakdown": token_breakdown,
        "compressed_token_breakdown": compressed_token_breakdown,
        "compression": compression_info,
        "context_preview": context_str[:2000],
        "available": True,
    }


# ── CodeGraphContext retrieval ────────────────────────────────────────

def _run_cgc(query: str, repo_path: str, top_k: int) -> Dict[str, Any]:
    """Run CodeGraphContext retrieval and return results + metrics."""
    try:
        from code_indexer.evaluator.cgc_connector import CGCConnector, CGC_AVAILABLE
        if not CGC_AVAILABLE:
            return {
                "results": [],
                "result_count": 0,
                "retrieval_time_ms": 0,
                "token_breakdown": {"system_prompt_tokens": 0, "context_tokens": 0, "query_tokens": 0, "total_tokens": 0},
                "available": False,
                "error": "codegraphcontext is not installed",
            }
    except ImportError:
        return {
            "results": [],
            "result_count": 0,
            "retrieval_time_ms": 0,
            "token_breakdown": {"system_prompt_tokens": 0, "context_tokens": 0, "query_tokens": 0, "total_tokens": 0},
            "available": False,
            "error": "codegraphcontext is not installed",
        }

    start = time.time()

    try:
        connector = CGCConnector(repo_path)

        # Get raw results with detail (use the finder directly for richer data)
        # We pass repo_path=None to search across all indexed code in CGC to avoid ABSOLUTE/RELATIVE path mismatches
        raw_results = connector.finder.find_related_code(
            user_query=query,
            fuzzy_search=False,
            edit_distance=2,
            repo_path=None,
        )

        matches = raw_results.get("ranked_results", [])[:top_k]
        retrieval_time = (time.time() - start) * 1000

        # Format results
        formatted_results = []
        context_parts = []
        for r in matches:
            name = r.get("name", "unknown")
            path = r.get("path", "unknown")
            line = r.get("line_number", "?")
            code = r.get("source", "")
            docstring = r.get("docstring", "")
            search_type = r.get("search_type", "")
            relevance = r.get("relevance_score", 0)
            is_dep = r.get("is_dependency", False)

            context_parts.append(
                f"File: {path} | Component: {name}\n"
                f"Lines {line}-?\n"
                f"```python\n{code}\n```"
            )

            formatted_results.append({
                "name": name,
                "file_path": path,
                "start_line": line,
                "end_line": "?",
                "code": code,
                "element_type": search_type,
                "description": docstring or "",
                "language": "python",
                "signature": "",
                "search_type": search_type,
                "relevance_score": relevance,
                "is_dependency": is_dep,
            })

        context_str = "\n\n".join(context_parts)
        token_breakdown = _tokens_for_context(query, context_str)

        return {
            "results": formatted_results,
            "result_count": len(formatted_results),
            "retrieval_time_ms": round(retrieval_time, 1),
            "token_breakdown": token_breakdown,
            "context_preview": context_str[:2000],
            "available": True,
            "total_matches": raw_results.get("total_matches", 0),
        }

    except Exception as e:
        logger.error(f"CGC retrieval failed: {e}")
        retrieval_time = (time.time() - start) * 1000
        return {
            "results": [],
            "result_count": 0,
            "retrieval_time_ms": round(retrieval_time, 1),
            "token_breakdown": {"system_prompt_tokens": 0, "context_tokens": 0, "query_tokens": 0, "total_tokens": 0},
            "available": False,
            "error": str(e),
        }


# ── Main comparison endpoint ──────────────────────────────────────────

@router.post("/compare")
async def compare_retrieval(request: CompareRequest):
    """Compare retrieval results from CodeIndexer and CodeGraphContext side-by-side."""
    from code_indexer.api.server import get_pipeline

    pipeline = get_pipeline()

    # Resolve repo path from CodeIndexer's metadata
    repo_path = ""
    if request.repo_name:
        meta = pipeline.cache.get_repo_metadata(request.repo_name)
        if meta:
            stats = meta.get("stats", {})
            repo_path = stats.get("repo_name", request.repo_name)
    
    # If no specific repo, try to find any indexed repo's path
    if not repo_path:
        all_meta = pipeline.cache.get_all_repo_metadata()
        if all_meta:
            # Use the first repo's path
            first = all_meta[0]
            repo_path = first.get("stats", {}).get("repo_name", "")

    # Run both retrievals in parallel
    ci_result, cgc_result = await asyncio.gather(
        asyncio.to_thread(
            _run_codeindexer, pipeline, request.query, request.repo_name, request.top_k
        ),
        asyncio.to_thread(
            _run_cgc, request.query, repo_path, request.top_k
        ),
    )

    return {
        "query": request.query,
        "repo_name": request.repo_name,
        "top_k": request.top_k,
        "codeindexer": ci_result,
        "codegraphcontext": cgc_result,
    }
