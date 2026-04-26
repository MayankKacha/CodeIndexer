# CodeIndexer MCP — VS Code Extension

A VS Code extension that exposes your CodeIndexer's graph search and semantic search capabilities as **MCP (Model Context Protocol) tools**, making them available to any LLM running in VS Code (GitHub Copilot, Gemini, etc.).

## Features

### 10 MCP Tools

The toolset is split between cheap **discovery** tools (compact metadata) and targeted **content** tools (full source code), so an LLM can navigate a codebase without burning context tokens until it needs the actual implementation.

| Tool | Returns | What it's for |
|------|---------|---------------|
| **`codebase_overview`** | Per-repo stats, languages, semantic confidence | First call — see what's indexed |
| **`search_code`** | Compact metadata, no source | Semantic search by natural-language query |
| **`find_symbol`** | Compact metadata, no source | Find functions/classes by exact or partial name |
| **`get_code`** | Full source code | Fetch implementation of a specific element |
| **`get_callers`** | Caller list, no source | Who calls this function? |
| **`get_callees`** | Callee list, no source | What does this function call? |
| **`get_impact`** | Direct + transitive callers, affected files | "If I change X, what else might break?" |
| **`get_call_chain`** | Shortest call path A → B | How are two functions connected? |
| **`get_file_structure`** | All elements in a file with signatures | Understand a file before reading it |
| **`find_dead_code`** | Functions with zero callers | Cleanup / orphan detection |

### Semantic Confidence Scoring

The extension automatically computes a **semantic confidence score (0–1)** based on how many code elements have meaningful comments/docstrings:

- **Score = 0.8** → 80% of functions have docs → semantic search is highly reliable
- **Score = 0.2** → only 20% have docs → graph search gets 80% weight in hybrid mode

## Installation

The extension ships with the CodeIndexer Python source bundled. On first activation it creates a managed virtual environment in VS Code's global storage and pip-installs everything for you. You only need:

- **Python 3.10+** on your `PATH`
- **Node.js** (only required if you're building the extension from source)

### From source

```bash
cd vscode-extension
npm install
npm run compile
npm run package
code --install-extension codeindexer-mcp-0.2.0.vsix
```

### What happens on first activation

1. Bundled Python source is detected → managed venv is created and dependencies are installed (one-time, ~5–10 min, mostly model downloads).
2. The FastAPI server starts on port 8000 (or a free ephemeral port if 8000 is busy).
3. Your current workspace is **auto-indexed** in the background — no command to run.
4. A file watcher kicks in: every save / create / delete triggers an incremental reindex of just that file. The Python side hash-checks first, so saves with no real changes are no-ops.

## How It Works

```
LLM (Copilot/Gemini) → MCP Protocol → MCP Server (Node.js, stdio)
                                            ↓ HTTP
                                    Python CodeIndexer API (FastAPI)
                                            ↓
                              ┌──────────────┼──────────────┐
                          NetworkX       Milvus Lite      BM25
                         (Graph DB)    (Vector DB)     (Keyword)
```

1. Extension activates → spawns Python CodeIndexer API server
2. Registers MCP server definition → VS Code discovers 3 tools
3. LLM calls MCP tools → MCP server → Python API → search results

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `codeindexer.pythonPath` | Auto-detect | Path to Python executable |
| `codeindexer.apiPort` | `8000` | Port for the CodeIndexer API server |
| `codeindexer.autoStartServer` | `true` | Auto-start API server on activation |

## Commands

Indexing is automatic; these are escape hatches.

- **CodeIndexer: Start API Server** — Restart the API server if it was stopped
- **CodeIndexer: Stop API Server** — Stop the API server (also stops the file watcher)

## Development

```bash
# Watch mode (auto-rebuild on changes)
npm run watch

# Type check
npx tsc --noEmit

# Package VSIX
npm run package
```
