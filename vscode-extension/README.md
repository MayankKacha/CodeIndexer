# CodeIndexer — Code Graph & Impact Analysis for AI

**Self-hosted code intelligence over MCP.** Indexes your repo into a hybrid graph + vector + keyword index so Copilot, Claude, Gemini, and Cursor can answer *"what calls this?"*, *"what breaks if I change X?"*, and *"where do I add Redis?"* with grounded, line-level citations — not hallucinations.

---

## Why CodeIndexer?

LLMs are great at reasoning about code, but they need *facts* about your repo first. Without grounding:

- "What calls `process_payment`?" → hallucinated callers
- "Where should I add a Redis cache?" → generic boilerplate
- "Is `legacy_auth` still used?" → wrong answer, no citations

CodeIndexer gives your LLM a real call graph, a semantic vector index, and a keyword index over your entire codebase — all exposed as MCP tools, available inside VS Code with zero manual setup.

---

## Zero-Install Setup

Open VS Code in any repo → the extension handles everything:

1. **Detects bundled Python source** → creates a managed virtual environment in VS Code's global storage
2. **Installs all dependencies automatically** (pip, models — one-time, ~5–10 min)
3. **Auto-indexes your workspace** in the background on first open
4. **File watcher** — every save/create/delete triggers an incremental reindex of just that file (SHA-256 hash-checked, so unchanged saves are instant no-ops)

You need: **Python 3.10+** on your `PATH`. That's it.

---

## 13 MCP Tools

Discovery tools return compact metadata (no source code) so the LLM can navigate cheaply. Content tools fetch the full implementation only when needed.

### Discovery

| Tool | What it answers |
|------|----------------|
| `codebase_overview` | What languages, how many elements, how reliable is semantic search? |
| `search_code` | *"Find functions that handle authentication"* — hybrid semantic + keyword |
| `find_symbol` | *"Find the class named UserRepository"* — by exact or partial name |
| `get_callers` | *"What calls `save_to_db`?"* — direct callers with file + line |
| `get_callees` | *"What does `process_order` call?"* — outgoing call list |
| `get_impact` | *"What breaks if I change `validate_token`?"* — transitive impact + affected files |
| `get_call_chain` | *"How are `main` and `send_email` connected?"* — shortest call path |
| `get_file_structure` | *"What's in `auth/middleware.py`?"* — all elements with signatures |
| `find_dead_code` | *"Any functions with zero callers?"* — orphan detection |

### Content

| Tool | What it returns |
|------|----------------|
| `get_code` | Full source of a specific function, method, or class |

### Test Coverage

| Tool | What it answers |
|------|----------------|
| `tests_for` | *"Which tests cover `calculate_discount`?"* — walks test→source edges |
| `tested_by` | *"What source does `test_checkout_flow` exercise?"* |

### Diff Analysis

| Tool | What it answers |
|------|----------------|
| `diff_impact` | Given a unified diff or two git refs, which elements are touched and who calls them? Designed for PR-review agents. |

---

## Example Conversations

> **You:** *"I want to add Redis caching to the order service. Where exactly?"*
>
> **LLM (using CodeIndexer):** Calls `search_code("order service data fetch")`, then `get_callers("get_order")`, then `get_impact("get_order")` → returns exact file paths and line numbers where a cache layer would intercept the most traffic.

---

> **You:** *"Is `legacy_report_builder` still used anywhere?"*
>
> **LLM:** Calls `get_callers("legacy_report_builder")` → zero results → cross-checks `find_dead_code` → confirms it's safe to delete.

---

> **You:** *"What tests cover the payment flow?"*
>
> **LLM:** Calls `tests_for("process_payment")` → returns test file, line numbers, and test function names.

---

## How It Works

```
LLM (Copilot / Claude / Gemini)
        │  MCP Protocol (stdio)
        ▼
  MCP Server (Node.js)
        │  HTTP
        ▼
  CodeIndexer API (FastAPI, Python)
        │
   ┌────┼─────────────┐
   ▼    ▼             ▼
NetworkX  Milvus Lite  BM25
(call graph) (vectors) (keywords)
```

- **Tree-sitter** parses Python, TypeScript, JavaScript, Java, C#, Go, Rust into `CodeElement`s
- **NetworkX** stores `CALLS`, `HAS_METHOD`, `INHERITS`, and `TESTS` edges for graph queries
- **Milvus Lite** stores 768-dim embeddings for semantic search
- **BM25** (rank-bm25) handles exact keyword and identifier search
- **Reciprocal Rank Fusion** merges results from all three indexes

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `codeindexer.pythonPath` | Auto-detect | Path to Python 3.10+ executable |
| `codeindexer.apiPort` | `8000` | Port for the CodeIndexer API server |
| `codeindexer.autoStartServer` | `true` | Start server automatically on activation |

---

## Commands

Indexing is fully automatic. These are escape hatches:

| Command | When to use |
|---------|-------------|
| **CodeIndexer: Start API Server** | Restart after a manual stop |
| **CodeIndexer: Stop API Server** | Shut down server and file watcher |

---

## Requirements

- VS Code 1.99+
- Python 3.10 or later on your `PATH`
- ~500 MB disk space for the managed venv and vector index (first-time setup)

---

## Building from Source

```bash
cd vscode-extension
npm install
npm run compile   # bundles Python source + compiles TypeScript
npm run package   # produces codeindexer-mcp-x.x.x.vsix
code --install-extension codeindexer-mcp-*.vsix
```

---

## Supported Languages

Python · TypeScript · JavaScript · Java · C# · Go · Rust

More via tree-sitter grammar additions — contributions welcome.

---

## Feedback & Issues

[github.com/codeindexer/codeindexer/issues](https://github.com/codeindexer/codeindexer/issues)
