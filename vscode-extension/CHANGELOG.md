# Changelog

All notable changes to CodeIndexer MCP are documented here.

## [0.2.0] — 2026-04-26

### Added
- **Auto-indexing on workspace open** — first-time activation now triggers a full background index of the current workspace. Returning users with an already-indexed repo skip straight to incremental updates.
- **Hash-based per-file reindex** — a `vscode.FileSystemWatcher` calls a new `POST /api/index/file` endpoint on save/create and `DELETE /api/index/file` on delete. The Python side compares SHA-256 hashes and no-ops unchanged saves, so editing in tight loops costs near zero.
- **Debounced reindex queue** (500 ms) so save bursts collapse to one call per file.
- **`update_file` on BM25 / `clear_file` on the graph store / `delete_by_file` on Milvus** — the building blocks that let a single file be cleanly replaced in every index.

### Removed
- **Manual `CodeIndexer: Index Current Workspace` command** — superseded by auto-indexing. Use the start/stop server commands as escape hatches if needed.

## [0.1.4] — 2026-04-26

### Changed
- **Auto-start enabled by default** — the API server now starts automatically when VS Code opens. First-time users get the install prompt immediately on activation; returning users get a ready-to-use server with no commands to run. Set `codeindexer.autoStartServer` to `false` to revert to lazy startup.

## [0.1.3] — 2026-04-26

### Added
- **Zero-install setup** — the CodeIndexer Python source is now bundled inside the extension. On first use, clicking "Install Automatically" creates a managed virtual environment in VS Code's global storage and pip-installs all dependencies. No manual `pip install` required.
- **Install progress notification** — live progress bar during installation, with all pip output streamed to the CodeIndexer MCP output channel.
- **Managed venv persistence** — the managed venv path is saved to global user settings and reused automatically on subsequent VS Code sessions.

## [0.1.2] — 2026-04-26

### Added
- **Pre-flight Python validation** — before spawning uvicorn, the extension now verifies the chosen Python can import `code_indexer` and `uvicorn`. Fails in <1s instead of waiting 30s for the uvicorn server to time out.
- **Actionable setup error** — when validation fails, the user sees a clear notification with `Open Settings` (jumps directly to `codeindexer.pythonPath`) and `View Output` buttons, so they know exactly what to fix.

## [0.1.1] — 2026-04-25

### Fixed
- **Marketplace install error** "Expected 'label' to be a non-empty string" — added the required `label` field to the `mcpServerDefinitionProviders` contribution and stopped mutating the resolved definition.
- **Duplicate caller/callee results** — `get_callers` and `get_callees` now dedupe by qualified name + file + line, so the same caller never appears twice. Backend graph store also uses keyed edges to make re-indexing idempotent.

## [0.1.0] — 2026-04-25

### Added
- 10 MCP tools exposed to VS Code LLMs (GitHub Copilot, Gemini, etc.):
  - `codebase_overview` — per-repo stats, languages, semantic confidence score
  - `search_code` — semantic search by natural-language query (compact results)
  - `find_symbol` — find functions/classes by exact or partial name
  - `get_code` — fetch full source code of a specific element
  - `get_callers` — find all direct callers of a function
  - `get_callees` — find all functions a given function calls
  - `get_impact` — transitive impact analysis ("what breaks if I change X?")
  - `get_call_chain` — shortest call path between two functions
  - `get_file_structure` — list all elements in a file with signatures
  - `find_dead_code` — find functions with zero callers
- Lazy Python API server startup — server only spawns when an MCP tool is first used
- Auto port-conflict resolution — falls back to a free ephemeral port if 8000 is taken
- Cross-platform Python venv detection (Windows `Scripts/`, Unix `bin/`)
- Commands: `Index Current Workspace`, `Start API Server`, `Stop API Server`
- Configuration: `pythonPath`, `apiPort`, `autoStartServer`
