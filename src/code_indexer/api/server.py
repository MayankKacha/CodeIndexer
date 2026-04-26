"""
FastAPI REST API server for CodeIndexer.

Provides HTTP endpoints for indexing (with SSE progress streaming),
chat (with streaming responses), analytics, and management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from code_indexer.api import metrics as _metrics

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CodeIndexer API",
    description="Advanced Code Intelligence API with Graph + Hybrid Search + Re-ranking + Compression",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Map URL routes to canonical tool names so /api/mcp/get-callers and the
# stdio MCP wrapper share metrics.
_ROUTE_TO_TOOL = {
    "/api/mcp/overview": "codebase_overview",
    "/api/mcp/search": "search_code",
    "/api/mcp/find-symbol": "find_symbol",
    "/api/mcp/get-code": "get_code",
    "/api/mcp/get-callers": "get_callers",
    "/api/mcp/get-callees": "get_callees",
    "/api/mcp/get-impact": "get_impact",
    "/api/mcp/get-call-chain": "get_call_chain",
    "/api/mcp/file-structure": "get_file_structure",
    "/api/mcp/dead-code": "find_dead_code",
    "/api/mcp/tests-for": "tests_for",
    "/api/mcp/tested-by": "tested_by",
    "/api/diff/impact": "diff_impact",
    "/api/index/file": "index_file",
    "/api/search": "search",
}


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Record latency for instrumented routes; pass everything else through."""
    tool = _ROUTE_TO_TOOL.get(request.url.path)
    if tool is None:
        return await call_next(request)

    start = time.perf_counter()
    error = False
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            error = True
        return response
    except Exception:
        error = True
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _metrics.record(tool, elapsed_ms, error=error)

# ── Global pipeline instance ───────────────────────────────────────────
_pipeline = None


def get_pipeline():
    """Get or create the pipeline singleton."""
    global _pipeline
    if _pipeline is None:
        from code_indexer.pipeline.indexer import CodeIndexerPipeline
        _pipeline = CodeIndexerPipeline()
    return _pipeline


# ── Request/Response Models ────────────────────────────────────────────


class IndexRequest(BaseModel):
    path: str = Field(..., description="Local directory path or GitHub URL")
    repo_name: str = Field(default="", description="Optional repository name")
    generate_descriptions: bool = Field(default=True, description="Generate LLM descriptions")


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    top_k: int = Field(default=10, description="Number of results")
    repo_name: str = Field(default="", description="Filter by repository")
    use_reranker: bool = Field(default=True, description="Apply cross-encoder re-ranking")
    use_compression: bool = Field(default=False, description="Apply query-aware compression")


class ChatRequest(BaseModel):
    query: str = Field(..., description="Question about the codebase")
    repo_name: str = Field(default="", description="Filter by repository")


class GraphQueryRequest(BaseModel):
    name: str = Field(..., description="Function/class name")
    repo_name: str = Field(default="", description="Filter by repository")


# ── Health ─────────────────────────────────────────────────────────────


@app.get("/api/health", tags=["Health"])
async def health():
    """Health check endpoint."""
    return {
        "service": "CodeIndexer API",
        "version": "2.0.0",
        "status": "running",
    }


@app.get("/api/metrics", tags=["Health"])
async def metrics():
    """Per-tool latency snapshot.

    Returns p50/p95/p99/count/error stats per instrumented endpoint, plus
    a simple `over_budget` flag so dashboards (or the LLM itself) can spot
    pathological tools at a glance. Stats are an in-memory rolling window
    of the last 1000 calls per tool.
    """
    return {"tools": _metrics.snapshot()}


# ── Indexing with SSE Progress ─────────────────────────────────────────


@app.post("/api/index", tags=["Indexing"])
async def index_codebase(request: IndexRequest):
    """Index a codebase and stream progress via Server-Sent Events."""

    async def event_stream():
        pipeline = get_pipeline()
        progress_events = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress_callback(step: str, message: str, data: dict = None):
            """Thread-safe callback that pushes events to the async queue."""
            event = {"step": step, "message": message, "data": data or {}}
            loop.call_soon_threadsafe(progress_events.put_nowait, event)

        async def run_indexing():
            try:
                stats = await asyncio.to_thread(
                    pipeline.index,
                    path=request.path,
                    repo_name=request.repo_name,
                    generate_descriptions=request.generate_descriptions,
                    progress_callback=progress_callback,
                )
                
                stats_dict = stats.to_dict()
                progress_events.put_nowait({"step": "done", "message": "Indexing finished.", "data": stats_dict})
            except Exception as e:
                logger.error(f"Indexing failed: {e}")
                progress_events.put_nowait({"step": "error", "message": str(e), "data": {}})

        # Start indexing in background
        task = asyncio.create_task(run_indexing())

        # Yield SSE events as they arrive
        while True:
            try:
                event = await asyncio.wait_for(progress_events.get(), timeout=120)
                yield f"data: {json.dumps(event)}\n\n"
                if event["step"] in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'step': 'heartbeat', 'message': 'Still working...', 'data': {}})}\n\n"

        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Diff-aware impact analysis ────────────────────────────────────────


class DiffImpactRequest(BaseModel):
    repo_name: str = Field(..., description="Repository name")
    diff_text: str = Field(default="", description="Unified diff text. If empty, base_ref is used.")
    base_ref: str = Field(default="", description="Git ref to diff against (e.g. main, HEAD~1)")
    head_ref: str = Field(default="HEAD", description="Git ref of the proposed state")
    max_depth: int = Field(default=3, description="Transitive caller depth")


@app.post("/api/diff/impact", tags=["Graph"])
async def diff_impact_endpoint(request: DiffImpactRequest):
    """Map a diff to the code elements it touches, then run impact analysis.

    Accepts either a raw unified-diff string or a (base_ref, head_ref) pair
    that resolves to `git diff base..head` in the repository's local clone.
    Returns the list of files touched, the indexed elements whose line ranges
    overlap the change, and per-element transitive caller closures.
    """
    from code_indexer.api.diff_impact import diff_impact as _diff_impact

    pipeline = get_pipeline()
    meta = pipeline.cache.get_repo_metadata(request.repo_name)
    repo_path: Optional[str] = None
    if meta:
        repo_path = meta.get("stats", {}).get("local_repo_path") or None

    try:
        return await asyncio.to_thread(
            _diff_impact,
            pipeline=pipeline,
            repo_name=request.repo_name,
            diff_text=request.diff_text or None,
            repo_path=repo_path,
            base_ref=request.base_ref or None,
            head_ref=request.head_ref or "HEAD",
            max_depth=request.max_depth,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"diff impact failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Per-file incremental reindex ──────────────────────────────────────


class FileIndexRequest(BaseModel):
    repo_name: str = Field(..., description="Repository name")
    file_path: str = Field(..., description="Absolute or repo-relative file path")
    repo_root: str = Field(default="", description="Optional explicit repo root (used on first call)")


@app.post("/api/index/file", tags=["Indexing"])
async def reindex_file(request: FileIndexRequest):
    """Re-index a single file. Hash-skips when content is unchanged.

    Used by the VS Code extension's file watcher: cheap to call on every save.
    """
    pipeline = get_pipeline()
    try:
        result = await asyncio.to_thread(
            pipeline.index_file,
            repo_name=request.repo_name,
            file_path=request.file_path,
            repo_root=request.repo_root or None,
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "unknown"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File reindex failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/index/file", tags=["Indexing"])
async def remove_file(repo_name: str, file_path: str):
    """Remove all index entries for a deleted file."""
    pipeline = get_pipeline()
    try:
        result = await asyncio.to_thread(pipeline.remove_file, repo_name, file_path)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "unknown"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File removal failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Chat with Streaming ───────────────────────────────────────────────


@app.post("/api/chat", tags=["Chat"])
async def chat(request: ChatRequest):
    """Ask a question about the codebase and stream the answer."""
    pipeline = get_pipeline()
    if not pipeline.rag_agent:
        raise HTTPException(status_code=400, detail="RAG Agent not configured. Set OPENAI_API_KEY.")

    async def stream_response():
        try:
            for chunk in pipeline.rag_agent.ask_stream(request.query, request.repo_name):
                yield chunk
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield f"\n\n[Error: {e}]"

    return StreamingResponse(stream_response(), media_type="text/plain")


# ── Search ─────────────────────────────────────────────────────────────


@app.post("/api/search", tags=["Search"])
async def search(request: SearchRequest):
    """Hybrid search across indexed codebases."""
    try:
        pipeline = get_pipeline()
        result = pipeline.search(
            query=request.query,
            top_k=request.top_k,
            repo_name=request.repo_name,
            use_reranker=request.use_reranker,
            use_compression=request.use_compression,
        )
        return result
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Analytics ──────────────────────────────────────────────────────────


@app.get("/api/analytics", tags=["Analytics"])
async def get_analytics():
    """Get aggregated analytics across all indexed repositories."""
    pipeline = get_pipeline()
    all_meta = pipeline.cache.get_all_repo_metadata()

    total_elements = 0
    total_lines = 0
    total_time = 0.0
    all_languages = {}
    repos_summary = []

    for meta in all_meta:
        s = meta.get("stats", {})
        total_elements += s.get("total_elements", 0)
        total_lines += s.get("total_lines", 0)
        total_time += s.get("indexing_time_seconds", 0)
        for lang, count in s.get("languages", {}).items():
            all_languages[lang] = all_languages.get(lang, 0) + count
        repos_summary.append({
            "repo_name": meta.get("repo_name", ""),
            "indexed_at": meta.get("indexed_at", ""),
            "total_elements": s.get("total_elements", 0),
            "total_lines": s.get("total_lines", 0),
            "indexing_time": s.get("indexing_time_seconds", 0),
            "functions": s.get("functions", 0),
            "methods": s.get("methods", 0),
            "classes": s.get("classes", 0),
            "parse_time": s.get("parse_time_seconds", 0),
            "embedding_time": s.get("embedding_time_seconds", 0),
            "graph_time": s.get("graph_time_seconds", 0),
            "vector_time": s.get("vector_time_seconds", 0),
        })

    return {
        "total_repositories": len(all_meta),
        "total_elements": total_elements,
        "total_lines": total_lines,
        "total_indexing_time": round(total_time, 2),
        "languages": all_languages,
        "repositories": repos_summary,
    }


# ── Repository Management ─────────────────────────────────────────────


@app.get("/api/repositories", tags=["Repositories"])
async def list_repositories():
    """List all indexed repositories with metadata."""
    pipeline = get_pipeline()
    all_meta = pipeline.cache.get_all_repo_metadata()

    repos = []
    for meta in all_meta:
        s = meta.get("stats", {})
        repos.append({
            "repo_name": meta.get("repo_name", ""),
            "indexed_at": meta.get("indexed_at", ""),
            "total_elements": s.get("total_elements", 0),
            "functions": s.get("functions", 0),
            "methods": s.get("methods", 0),
            "classes": s.get("classes", 0),
            "total_lines": s.get("total_lines", 0),
            "languages": s.get("languages", {}),
            "indexing_time": s.get("indexing_time_seconds", 0),
            "is_incremental": s.get("is_incremental", False),
        })
    return {"repositories": repos}


@app.delete("/api/repositories/{repo_name}", tags=["Repositories"])
async def delete_repository(repo_name: str):
    """Delete an indexed repository."""
    try:
        pipeline = get_pipeline()
        pipeline.delete_repository(repo_name)
        return {"status": "deleted", "repo_name": repo_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats", tags=["Management"])
async def get_stats():
    """Get system statistics."""
    try:
        pipeline = get_pipeline()
        stats = pipeline.get_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Graph Endpoints ────────────────────────────────────────────────────


@app.post("/api/graph/callers", tags=["Graph"])
async def find_callers(request: GraphQueryRequest):
    """Find all callers of a function/method."""
    try:
        pipeline = get_pipeline()
        callers = pipeline.find_callers(request.name, request.repo_name)
        return {"function": request.name, "callers": callers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/graph/impact", tags=["Graph"])
async def impact_analysis(request: GraphQueryRequest):
    """Analyze the impact of changing a function."""
    try:
        pipeline = get_pipeline()
        impact = pipeline.impact_analysis(request.name)
        return impact
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── MCP Support Endpoints ──────────────────────────────────────────────
# Design: Each endpoint does ONE thing and returns MINIMAL data.
# "Discovery" endpoints return metadata only (names, locations, signatures).
# "Content" endpoints return actual source code.
# This lets LLMs conserve context window by fetching code only when needed.


class SymbolRequest(BaseModel):
    name: str = Field(..., description="Exact or partial function/class name")
    repo_name: str = Field(default="", description="Filter by repository")


class CallChainRequest(BaseModel):
    from_name: str = Field(..., description="Source function name")
    to_name: str = Field(..., description="Target function name")
    max_depth: int = Field(default=10, description="Maximum call chain depth")


class FileStructureRequest(BaseModel):
    file_path: str = Field(..., description="File path (relative or absolute)")
    repo_name: str = Field(default="", description="Filter by repository")


# ── 1. Codebase Overview ───────────────────────────────────────────────

@app.get("/api/mcp/overview", tags=["MCP"])
async def codebase_overview(repo_name: str = ""):
    """Get a compact codebase overview: repos, stats, languages, semantic confidence.

    This is the FIRST tool an LLM should call to understand the codebase.
    Returns everything needed to decide which other tools to use.
    """
    try:
        pipeline = get_pipeline()
        all_meta = pipeline.cache.get_all_repo_metadata()
        store = pipeline.graph_store

        repos = []
        for meta in all_meta:
            rn = meta.get("repo_name", "")
            if repo_name and rn != repo_name:
                continue
            s = meta.get("stats", {})

            # Compute semantic confidence
            total = 0
            with_docs = 0
            for _, d in store.graph.nodes(data=True):
                if d.get("label") == "Repository":
                    continue
                if d.get("repo_name") != rn:
                    continue
                if d.get("element_type") not in ("function", "method", "class"):
                    continue
                total += 1
                docstring = d.get("docstring", "") or ""
                description = d.get("description", "") or ""
                if len(docstring.strip()) > 10 or len(description.strip()) > 10:
                    with_docs += 1

            confidence = round(with_docs / total, 4) if total > 0 else 0.0

            repos.append({
                "repo_name": rn,
                "indexed_at": meta.get("indexed_at", ""),
                "total_elements": s.get("total_elements", 0),
                "functions": s.get("functions", 0),
                "methods": s.get("methods", 0),
                "classes": s.get("classes", 0),
                "total_files": s.get("total_files", 0),
                "total_lines": s.get("total_lines", 0),
                "languages": s.get("languages", {}),
                "semantic_confidence": confidence,
                "elements_with_docs": with_docs,
            })

        return {"repositories": repos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 2. Search Code (semantic discovery — NO full code) ─────────────────

@app.post("/api/mcp/search", tags=["MCP"])
async def mcp_search(request: SearchRequest):
    """Semantic search: find code by natural language description.

    Returns COMPACT results: name, type, file, line, signature, description.
    Does NOT return full source code (use get_code for that).
    This keeps the response small so the LLM can decide which results to inspect.
    """
    try:
        pipeline = get_pipeline()
        candidates = pipeline.hybrid_search.search(
            query=request.query,
            top_k=request.top_k,
            repo_name=request.repo_name,
            use_bm25=True,
            use_vector=True,
            use_graph=False,
            rerank=request.use_reranker,
        )
        if request.use_reranker and len(candidates) > 1:
            try:
                results = pipeline.reranker.rerank(
                    query=request.query, results=candidates, top_k=request.top_k
                )
            except Exception:
                results = candidates[: request.top_k]
        else:
            results = candidates[: request.top_k]

        # Strip full code from results — return compact metadata only
        compact = []
        for r in results:
            compact.append({
                "name": r.get("name", ""),
                "qualified_name": r.get("qualified_name", ""),
                "element_type": r.get("element_type", ""),
                "file_path": r.get("file_path", ""),
                "start_line": r.get("start_line", 0),
                "end_line": r.get("end_line", 0),
                "language": r.get("language", ""),
                "signature": r.get("signature", ""),
                "description": r.get("description", ""),
                "score": r.get("score", r.get("rrf_score", 0)),
            })

        return {"query": request.query, "results": compact, "total": len(compact)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 3. Find Symbol (exact/partial name lookup — NO full code) ──────────

@app.post("/api/mcp/find-symbol", tags=["MCP"])
async def mcp_find_symbol(request: SymbolRequest):
    """Find code elements by exact or partial name.

    Returns compact metadata: name, type, file, lines, signature.
    Use this when you know the function/class name (or part of it).
    """
    try:
        pipeline = get_pipeline()
        gq = pipeline.graph_queries

        # Try exact match first
        exact = gq.find_by_name(request.name, request.repo_name)
        # Then pattern match
        pattern = gq.search_by_pattern(request.name, request.repo_name)

        seen = set()
        results = []

        def add_node(node, match_type):
            key = node.get("element_id", node.get("name", ""))
            if key in seen:
                return
            seen.add(key)
            results.append({
                "name": node.get("name", ""),
                "qualified_name": node.get("qualified_name", ""),
                "element_type": node.get("element_type", ""),
                "file_path": node.get("file_path", ""),
                "start_line": node.get("start_line", 0),
                "end_line": node.get("end_line", 0),
                "language": node.get("language", ""),
                "signature": node.get("signature", ""),
                "description": node.get("description", ""),
                "parent_class": node.get("parent_class", ""),
                "complexity": node.get("complexity", 0),
                "match_type": match_type,
            })

        for r in exact:
            node = r.get("e", {})
            if hasattr(node, "__getitem__"):
                add_node(node, "exact")

        for r in pattern:
            node = r.get("e", {})
            if hasattr(node, "__getitem__"):
                add_node(node, "pattern")

        return {"symbol": request.name, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 4. Get Code (retrieve actual source code) ─────────────────────────

@app.post("/api/mcp/get-code", tags=["MCP"])
async def mcp_get_code(request: SymbolRequest):
    """Get the FULL source code of a specific function/class by name.

    Use this AFTER using find-symbol or search to identify WHICH element you need.
    Returns the complete source code, docstring, signature, and metadata.
    """
    try:
        pipeline = get_pipeline()
        gq = pipeline.graph_queries

        matches = gq.find_by_name(request.name, request.repo_name)
        if not matches:
            # Fallback to pattern search
            pattern = gq.search_by_pattern(request.name, request.repo_name)
            matches = pattern

        results = []
        for r in matches:
            node = r.get("e", {})
            if hasattr(node, "__getitem__"):
                results.append({
                    "name": node.get("name", ""),
                    "qualified_name": node.get("qualified_name", ""),
                    "element_type": node.get("element_type", ""),
                    "file_path": node.get("file_path", ""),
                    "start_line": node.get("start_line", 0),
                    "end_line": node.get("end_line", 0),
                    "language": node.get("language", ""),
                    "signature": node.get("signature", ""),
                    "docstring": node.get("docstring", ""),
                    "description": node.get("description", ""),
                    "code": node.get("code", ""),
                    "parent_class": node.get("parent_class", ""),
                    "complexity": node.get("complexity", 0),
                })

        return {"symbol": request.name, "results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 5. Get Callers (who calls this?) ───────────────────────────────────

@app.post("/api/mcp/get-callers", tags=["MCP"])
async def mcp_get_callers(request: SymbolRequest):
    """Find all direct callers of a function/method.

    Returns: caller name, type, file, line. Compact — no source code.
    """
    try:
        pipeline = get_pipeline()
        callers = pipeline.find_callers(request.name, request.repo_name)
        return {
            "target": request.name,
            "callers": callers,
            "total": len(callers),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 6. Get Callees (what does this call?) ──────────────────────────────

@app.post("/api/mcp/get-callees", tags=["MCP"])
async def mcp_get_callees(request: SymbolRequest):
    """Find all functions/methods called BY a given function.

    Returns: callee name, type, file, line. Compact — no source code.
    """
    try:
        pipeline = get_pipeline()
        callees = pipeline.find_callees(request.name, request.repo_name)
        return {
            "source": request.name,
            "callees": callees,
            "total": len(callees),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 6b. Tests for / Tested by (test ↔ source coverage) ────────────────

@app.post("/api/mcp/tests-for", tags=["MCP"])
async def mcp_tests_for(request: SymbolRequest):
    """Find tests that exercise the source element `name`.

    Walks `TESTS` edges directly (no transitive closure). Useful for
    "what tests should I run if I change this function?" queries.
    """
    try:
        pipeline = get_pipeline()
        tests = pipeline.graph_queries.tests_for(request.name, request.repo_name)
        return {"target": request.name, "tests": tests, "total": len(tests)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mcp/tested-by", tags=["MCP"])
async def mcp_tested_by(request: SymbolRequest):
    """Find source elements exercised by the test `name`.

    Mirror of tests-for: given a test, what production code does it cover?
    """
    try:
        pipeline = get_pipeline()
        covers = pipeline.graph_queries.tested_by(request.name, request.repo_name)
        return {"test": request.name, "covers": covers, "total": len(covers)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 7. Get Impact Analysis ─────────────────────────────────────────────

@app.post("/api/mcp/get-impact", tags=["MCP"])
async def mcp_get_impact(request: SymbolRequest):
    """Full impact analysis: what breaks if this function changes?

    Returns direct callers, transitive callers, and all affected files.
    This is the key tool for answering "what will be impacted if I change X?"
    """
    try:
        pipeline = get_pipeline()
        impact = pipeline.impact_analysis(request.name)
        return impact
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 8. Get Call Chain ──────────────────────────────────────────────────

@app.post("/api/mcp/get-call-chain", tags=["MCP"])
async def mcp_get_call_chain(request: CallChainRequest):
    """Find the call path between two functions (A → B → C → D).

    Returns the shortest call chain connecting from_name to to_name.
    """
    try:
        pipeline = get_pipeline()
        chain = pipeline.find_call_chain(request.from_name, request.to_name)
        return {
            "from": request.from_name,
            "to": request.to_name,
            "chain": chain,
            "found": len(chain) > 0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 9. Get File Structure ─────────────────────────────────────────────

@app.post("/api/mcp/file-structure", tags=["MCP"])
async def mcp_file_structure(request: FileStructureRequest):
    """List all functions, methods, and classes in a specific file.

    Returns compact metadata (name, type, line numbers, signature).
    Use this to understand the structure of a file before diving into specifics.
    """
    try:
        pipeline = get_pipeline()
        store = pipeline.graph_store

        elements = []
        for _, d in store.graph.nodes(data=True):
            if d.get("label") == "Repository":
                continue
            node_file = d.get("file_path", "")
            # Match if the requested path is a suffix of the stored path or vice versa
            if not (node_file.endswith(request.file_path)
                    or request.file_path.endswith(node_file)
                    or node_file == request.file_path):
                continue
            if request.repo_name and d.get("repo_name") != request.repo_name:
                continue
            elements.append({
                "name": d.get("name", ""),
                "qualified_name": d.get("qualified_name", ""),
                "element_type": d.get("element_type", ""),
                "start_line": d.get("start_line", 0),
                "end_line": d.get("end_line", 0),
                "signature": d.get("signature", ""),
                "parent_class": d.get("parent_class", ""),
                "complexity": d.get("complexity", 0),
                "has_docstring": bool((d.get("docstring", "") or "").strip()),
            })

        elements.sort(key=lambda x: x.get("start_line", 0))
        return {
            "file_path": request.file_path,
            "elements": elements,
            "total": len(elements),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 10. Find Dead Code ────────────────────────────────────────────────

@app.post("/api/mcp/dead-code", tags=["MCP"])
async def mcp_dead_code(request: SymbolRequest):
    """Find potentially dead/unused functions and methods.

    Returns functions that have zero callers in the code graph.
    Useful for cleanup and understanding which code is actually used.
    """
    try:
        pipeline = get_pipeline()
        dead = pipeline.find_dead_code(request.repo_name)
        return {
            "repo_name": request.repo_name,
            "dead_code": dead,
            "total": len(dead),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



