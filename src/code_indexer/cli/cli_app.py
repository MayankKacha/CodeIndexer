"""
Typer CLI application for CodeIndexer.

Provides a rich command-line interface for indexing, searching,
graph analysis, and management operations.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    name="codeindexer",
    help="🔍 CodeIndexer — Advanced Code Intelligence with Graph + Hybrid Search + Re-ranking",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


def setup_logging(verbose: bool = False):
    """Configure logging with Rich."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )


def get_pipeline():
    """Get the pipeline instance."""
    from code_indexer.pipeline.indexer import CodeIndexerPipeline
    return CodeIndexerPipeline()


# ── Index Command ──────────────────────────────────────────────────────


@app.command()
def index(
    path: str = typer.Argument(..., help="Local directory path or GitHub URL to index"),
    repo_name: str = typer.Option("", "--name", "-n", help="Repository name"),
    no_descriptions: bool = typer.Option(False, "--no-desc", help="Skip LLM description generation"),
    no_neo4j: bool = typer.Option(False, "--no-graph", help="Skip Neo4j storage"),
    no_milvus: bool = typer.Option(False, "--no-vectors", help="Skip Milvus storage"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """📥 Index a codebase (local directory or GitHub URL)."""
    setup_logging(verbose)

    console.print(
        Panel(
            f"[bold cyan]Indexing:[/bold cyan] {path}",
            title="🚀 CodeIndexer",
            border_style="cyan",
        )
    )

    pipeline = get_pipeline()

    try:
        stats = pipeline.index(
            path=path,
            repo_name=repo_name,
            generate_descriptions=not no_descriptions,
            use_neo4j=not no_neo4j,
            use_milvus=not no_milvus,
        )

        # Display results
        table = Table(title="📊 Indexing Results", show_header=True, border_style="green")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Repository", stats.repo_name)
        table.add_row("Total Elements", str(stats.total_elements))
        table.add_row("Functions", str(stats.functions))
        table.add_row("Methods", str(stats.methods))
        table.add_row("Classes", str(stats.classes))
        table.add_row("Total Lines", str(stats.total_lines))
        table.add_row("Embeddings", str(stats.embedding_count))
        table.add_row("Graph Nodes", str(stats.graph_nodes))
        table.add_row("Graph Relationships", str(stats.graph_relationships))
        table.add_row("Languages", ", ".join(f"{k}({v})" for k, v in stats.languages.items()))
        table.add_row("Time", f"{stats.indexing_time_seconds}s")

        console.print(table)

        if stats.errors:
            console.print(f"\n[yellow]⚠️ Errors: {len(stats.errors)}[/yellow]")
            for err in stats.errors:
                console.print(f"  • {err}", style="yellow")

        console.print("\n[green]✅ Indexing complete![/green]")

    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        pipeline.close()


# ── Search Command ─────────────────────────────────────────────────────


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(10, "--top", "-k", help="Number of results"),
    repo_name: str = typer.Option("", "--repo", "-r", help="Filter by repository"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Skip re-ranking"),
    compress: bool = typer.Option(False, "--compress", "-c", help="Apply query-aware compression"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔍 Search indexed codebases with hybrid search."""
    setup_logging(verbose)

    pipeline = get_pipeline()

    try:
        result = pipeline.search(
            query=query,
            top_k=top_k,
            repo_name=repo_name,
            use_reranker=not no_rerank,
            use_compression=compress,
        )

        if json_output:
            console.print_json(json.dumps(result, indent=2, default=str))
            return

        results = result.get("results", [])
        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        cached_label = " [dim](cached)[/dim]" if result.get("from_cache") else ""
        console.print(
            Panel(
                f"[bold cyan]Query:[/bold cyan] {query}{cached_label}",
                title=f"🔍 {len(results)} Results",
                border_style="cyan",
            )
        )

        for i, r in enumerate(results, 1):
            name = r.get("qualified_name") or r.get("name", "unknown")
            etype = r.get("element_type", "")
            file_path = r.get("file_path", "")
            start_line = r.get("start_line", "?")
            end_line = r.get("end_line", "?")
            description = r.get("description", "")
            score = r.get("rerank_score") or r.get("rrf_score") or r.get("score", 0)

            console.print(f"\n[bold green]#{i}[/bold green] [bold]{etype.title()}:[/bold] [cyan]{name}[/cyan]")
            console.print(f"   📍 {file_path} | Lines {start_line}–{end_line}")

            if description:
                console.print(f"   📝 {description}")

            if score:
                console.print(f"   ⭐ Score: {score:.4f}")

            if r.get("signature"):
                console.print(f"   [dim]{r['signature'][:100]}[/dim]")

        # Show compression stats
        compression = result.get("compression")
        if compression:
            console.print(
                Panel(
                    f"Original: {compression['original_tokens']} tokens → "
                    f"Compressed: {compression['compressed_tokens']} tokens "
                    f"([green]{compression['compression_ratio']}% reduction[/green])",
                    title="🗜️ Compression",
                    border_style="magenta",
                )
            )
            if compress:
                console.print("\n[bold]Compressed Context:[/bold]")
                console.print(compression.get("compressed_context", ""))

    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        pipeline.close()


@app.command()
def ask(
    query: str = typer.Argument(..., help="Question to ask the RAG Assistant"),
    repo_name: str = typer.Option("", "--repo", "-r", help="Filter by repository"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🤖 Ask a question about the indexed codebase."""
    setup_logging(verbose)
    pipeline = get_pipeline()

    if not pipeline.rag_agent:
        console.print("[red]❌ RAG Agent not configured. Please set OPENAI_API_KEY.[/red]")
        raise typer.Exit(1)

    console.print(Panel(f"[bold cyan]Asking:[/bold cyan] {query}", border_style="cyan"))
    
    try:
        # We write directly to stdout buffer to stream word by word effectively
        for chunk in pipeline.rag_agent.ask_stream(query, repo_name):
            sys.stdout.write(chunk)
            sys.stdout.flush()
        print()  # Final newline
    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        pipeline.close()


@app.command()
def recommend(
    requirement: str = typer.Argument(..., help="Feature/Fix requirement"),
    repo_name: str = typer.Option("", "--repo", "-r", help="Filter by repository"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """💡 Get code change recommendations to implement a feature or fix a bug."""
    setup_logging(verbose)
    pipeline = get_pipeline()

    if not pipeline.rag_agent:
        console.print("[red]❌ RAG Agent not configured. Please set OPENAI_API_KEY.[/red]")
        raise typer.Exit(1)

    console.print(Panel(f"[bold magenta]Requirement:[/bold magenta] {requirement}", border_style="magenta"))
    
    try:
        for chunk in pipeline.rag_agent.recommend_stream(requirement, repo_name):
            sys.stdout.write(chunk)
            sys.stdout.flush()
        print()
    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        pipeline.close()


# ── Find Command ───────────────────────────────────────────────────────


@app.command()
def find(
    name: str = typer.Argument(..., help="Function/class name to find"),
    repo_name: str = typer.Option("", "--repo", "-r", help="Filter by repository"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🎯 Find a specific function or class by name."""
    setup_logging(verbose)

    pipeline = get_pipeline()
    try:
        results = pipeline.hybrid_search.search_exact(name, repo_name)

        if not results:
            console.print(f"[yellow]No results found for '{name}'[/yellow]")
            return

        for r in results:
            console.print(Panel(
                f"[bold]{r.get('element_type', '').title()}:[/bold] [cyan]{r.get('qualified_name', r.get('name', ''))}[/cyan]\n"
                f"📍 {r.get('file_path', '')} | Lines {r.get('start_line', '?')}–{r.get('end_line', '?')}\n"
                f"📝 {r.get('description', 'No description')}\n\n"
                f"[dim]{r.get('code', '')[:500]}[/dim]",
                border_style="green",
            ))
    finally:
        pipeline.close()


# ── Graph Analysis Commands ────────────────────────────────────────────


@app.command()
def callers(
    name: str = typer.Argument(..., help="Function/method name"),
    repo_name: str = typer.Option("", "--repo", "-r"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📞 Find all callers of a function/method."""
    setup_logging(verbose)
    pipeline = get_pipeline()
    try:
        results = pipeline.find_callers(name, repo_name)
        if not results:
            console.print(f"[yellow]No callers found for '{name}'[/yellow]")
            return

        table = Table(title=f"📞 Callers of {name}", border_style="cyan")
        table.add_column("Caller", style="green")
        table.add_column("Type", style="cyan")
        table.add_column("File", style="white")
        table.add_column("Line", style="yellow")

        for r in results:
            table.add_row(
                r.get("caller_qualified_name", r.get("caller_name", "")),
                r.get("caller_type", ""),
                r.get("caller_file", ""),
                str(r.get("caller_line", "")),
            )
        console.print(table)
    finally:
        pipeline.close()


@app.command()
def callees(
    name: str = typer.Argument(..., help="Function/method name"),
    repo_name: str = typer.Option("", "--repo", "-r"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📤 Find all functions called by a function."""
    setup_logging(verbose)
    pipeline = get_pipeline()
    try:
        results = pipeline.find_callees(name, repo_name)
        if not results:
            console.print(f"[yellow]No callees found for '{name}'[/yellow]")
            return

        table = Table(title=f"📤 Callees of {name}", border_style="cyan")
        table.add_column("Called Function", style="green")
        table.add_column("Type", style="cyan")
        table.add_column("File", style="white")
        table.add_column("Line", style="yellow")

        for r in results:
            table.add_row(
                r.get("callee_qualified_name", r.get("callee_name", "")),
                r.get("callee_type", ""),
                r.get("callee_file", ""),
                str(r.get("callee_line", "")),
            )
        console.print(table)
    finally:
        pipeline.close()


@app.command()
def impact(
    name: str = typer.Argument(..., help="Function/method name to analyze"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """💥 Analyze the impact of changing a function."""
    setup_logging(verbose)
    pipeline = get_pipeline()
    try:
        result = pipeline.impact_analysis(name)

        console.print(Panel(
            f"[bold]Target:[/bold] {result.get('target', name)}\n"
            f"[bold]Direct Callers:[/bold] [yellow]{result.get('direct_callers', 0)}[/yellow]\n"
            f"[bold]Total Affected:[/bold] [red]{result.get('total_affected', 0)}[/red]\n"
            f"[bold]Affected Files:[/bold] {len(result.get('affected_files', []))}",
            title="💥 Impact Analysis",
            border_style="red",
        ))

        files = result.get("affected_files", [])
        if files:
            console.print("\n[bold]Affected Files:[/bold]")
            for f in files:
                console.print(f"  📄 {f}")
    finally:
        pipeline.close()


# ── Management Commands ────────────────────────────────────────────────


@app.command(name="list")
def list_repos(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """📋 List all indexed repositories."""
    setup_logging(verbose)
    pipeline = get_pipeline()
    try:
        repos = pipeline.list_repositories()
        if not repos:
            console.print("[yellow]No repositories indexed yet.[/yellow]")
            return

        table = Table(title="📋 Indexed Repositories", border_style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Elements", style="white")
        table.add_column("Updated", style="dim")

        for r in repos:
            table.add_row(
                str(r.get("name", "")),
                str(r.get("element_count", 0)),
                str(r.get("updated_at", "")),
            )
        console.print(table)
    finally:
        pipeline.close()


@app.command()
def delete(
    repo_name: str = typer.Argument(..., help="Repository name to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """🗑️ Delete an indexed repository."""
    if not force:
        confirm = typer.confirm(f"Delete repository '{repo_name}'?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            return

    pipeline = get_pipeline()
    try:
        pipeline.delete_repository(repo_name)
        console.print(f"[green]✅ Deleted repository: {repo_name}[/green]")
    finally:
        pipeline.close()


@app.command()
def stats(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """📊 Show system statistics."""
    setup_logging(verbose)
    pipeline = get_pipeline()
    try:
        s = pipeline.get_stats()
        console.print_json(json.dumps(s, indent=2, default=str))
    finally:
        pipeline.close()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """🌐 Start the API server."""
    import uvicorn

    console.print(
        Panel(
            f"[bold cyan]Starting API server at http://{host}:{port}[/bold cyan]\n"
            f"📖 Docs: http://{host}:{port}/docs",
            title="🌐 CodeIndexer API",
            border_style="cyan",
        )
    )

    uvicorn.run(
        "code_indexer.api.server:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def watch(
    path: str = typer.Argument(".", help="Directory to watch"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """👁️ Watch a directory for changes and update index."""
    setup_logging(verbose)

    from code_indexer.pipeline.file_watcher import FileWatcher
    from code_indexer.parsing.code_splitter import split_file

    pipeline = get_pipeline()

    def on_change(file_path: str, event_type: str):
        console.print(f"[cyan]📝 {event_type}:[/cyan] {file_path}")
        if event_type != "deleted":
            try:
                parsed = split_file(file_path, repo_name=Path(path).name)
                if parsed.elements:
                    console.print(f"  → Re-indexed {len(parsed.elements)} elements")
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")

    console.print(
        Panel(
            f"Watching [cyan]{Path(path).resolve()}[/cyan] for changes...\n"
            "Press Ctrl+C to stop.",
            title="👁️ File Watcher",
            border_style="cyan",
        )
    )

    watcher = FileWatcher(path, on_change)
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher stopped.[/yellow]")
    finally:
        pipeline.close()


if __name__ == "__main__":
    app()
