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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path

logger = logging.getLogger(__name__)

# Import comparison router
from code_indexer.api.comparison import router as comparison_router

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

# Register comparison router
app.include_router(comparison_router)

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
    index_with_cgc: bool = Field(default=False, description="Also index with CodeGraphContext")


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
                
                # Check if we should also index with CodeGraphContext
                if request.index_with_cgc and stats.local_repo_path:
                    progress_events.put_nowait({"step": "cgc_start", "message": "Starting CodeGraphContext partitioning & indexing...", "data": {}})
                    cgc_start = time.time()
                    try:
                        import subprocess
                        import sys
                        import os
                        env = os.environ.copy()
                        env["DATABASE_TYPE"] = "kuzudb"
                        
                        # Find the cgc executable in the current python env
                        cgc_exe = os.path.join(os.path.dirname(sys.executable), "cgc")
                        if not os.path.exists(cgc_exe):
                            # Fallback just in case
                            cgc_exe = "cgc"
                            
                        process = await asyncio.to_thread(
                            subprocess.run,
                            [cgc_exe, "index", stats.local_repo_path],
                            env=env,
                            cwd=stats.local_repo_path,
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        cgc_time = time.time() - cgc_start
                        
                        if process.returncode != 0:
                            logger.error(f"CGC indexing failed: {process.stderr}")
                            progress_events.put_nowait({"step": "error", "message": f"CodeGraphContext indexing failed ({cgc_time:.1f}s)", "data": {"stderr": process.stderr}})
                        else:
                            stats_dict["cgc_indexing_time_seconds"] = round(cgc_time, 2)
                            progress_events.put_nowait({"step": "cgc_done", "message": f"CodeGraphContext indexed successfully in {cgc_time:.1f}s", "data": {"seconds": cgc_time}})
                            
                    except Exception as e:
                        logger.error(f"Failed to run CGC indexing: {e}")
                        progress_events.put_nowait({"step": "error", "message": f"CGC Error: {str(e)}", "data": {}})
                
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


# ── Serve React Static Files (Production) ─────────────────────────────

_web_dist = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist"
if _web_dist.exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="assets")

    # SPA catch-all: serve index.html for any non-API route
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", tags=["SPA"])
    async def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        # Check if this is a real static file
        file_path = _web_dist / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise serve index.html (React Router handles routing)
        return FileResponse(str(_web_dist / "index.html"))

