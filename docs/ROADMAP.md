# CodeIndexer Roadmap

This document tracks the deferred items from the v0.2.x slim-down + auto-index work. Each entry has a clear scope, success criteria, and a sketch of the implementation so anyone (you, me, or a future contributor) can pick it up cold.

---

## 1. LSP-grounded call graph (Python → Java → C#)

### Why
Tree-sitter gives us *syntax* (this token is a call) but not *resolution* (which symbol does it bind to?). For dynamic dispatch, decorated methods, polymorphism, and most cross-file references, the current heuristic is wrong silently. Wrong edges are worse than missing edges because impact analysis becomes unreliable, which is the core promise of the tool.

The fix is to ask a real language server. LSP gives us `textDocument/references`, `textDocument/definition`, and `callHierarchy/incomingCalls` — which are exactly the edges we want.

### Phasing
Ship one language at a time. Each is its own PR.

1. **Python (pyright or jedi-language-server)** — pyright preferred, runs as a node binary, well-documented LSP.
2. **Java (eclipse.jdt.ls)** — heavier (JVM), but rock-solid and ships call-hierarchy out of the box.
3. **C# (omnisharp-roslyn or csharp-ls)** — omnisharp is older but maintained; csharp-ls is lighter and uses Roslyn directly.

### Implementation sketch (per language)

```
src/code_indexer/lsp/
    __init__.py
    client.py           # generic LSP JSON-RPC client over stdio
    pyright_resolver.py # spawns pyright-langserver, drives it
    jdtls_resolver.py
    omnisharp_resolver.py
    resolver_factory.py # picks resolver by language
```

Wire it into the indexing pipeline as an opt-in second pass after tree-sitter:

1. `CodeIndexerPipeline.index()` runs tree-sitter as today and builds `CodeElement`s.
2. If `settings.use_lsp_resolver`, spawn the language server, send `initialize` + per-file `didOpen`.
3. For each tree-sitter `calls` entry, query `callHierarchy/outgoingCalls` on the source span and replace the heuristic match with the LSP-resolved target's `qualified_name`.
4. Tear down the language server.

### Success criteria
- On a sample Django repo, `find_callers(some_view)` returns the URL-router entry that today's heuristic misses.
- Indexing time within 3× of the tree-sitter-only path on a 50K-LoC repo.
- No regression on languages we *don't* have a resolver for (Go, Rust, etc.) — they keep tree-sitter heuristics.

### Risks / edge cases
- **Bundling language servers.** Do we ship them or detect them on the user's `PATH`? Prefer detection with a clear error if missing; bundling gets us into platform-binary distribution territory.
- **Cold-start cost.** JDT.LS takes 10–20s to come up; cache its workspace symbols across reindexes.
- **Partial answers.** If LSP times out for a file, fall back to the tree-sitter heuristic for that file rather than dropping edges.

---

## 2. `sqlite-vec` migration (Milvus Lite → SQLite + sqlite-vec)

### Why
Milvus Lite produces a 600 MB database file for our example workspace, ships its own daemon, and creates a `.db.lock` that breaks on hard kill. `sqlite-vec` is a single SQLite extension that gives us ANN search via brute-force or HNSW, stays under tens of megabytes for the same data, and has no out-of-process state.

### Strategy: parallel store, default-flip later

Don't do an in-place migration. Add a second store class behind the same interface, expose a config flag, dual-write while users opt in, and only delete `MilvusStore` after a release of bake time.

```python
# src/code_indexer/vectors/sqlite_vec_store.py

class SqliteVecStore:
    def __init__(self, db_path: str, embedding_dim: int = 768): ...
    def insert_elements(self, elements, embeddings) -> int: ...
    def search(self, query_embedding, top_k, filter_expr=None): ...
    def search_by_repo(self, query_embedding, repo_name, top_k): ...
    def delete_by_repo(self, repo_name): ...
    def delete_by_file(self, repo_name, file_path): ...
```

Config:

```python
class Settings(BaseSettings):
    vector_backend: Literal["milvus", "sqlite_vec"] = "milvus"
```

### Phases
1. **Land `SqliteVecStore` behind the flag** with full parity for the methods `MilvusStore` exposes today (insert, search, search_by_repo, delete_by_repo, delete_by_file). Cover with unit tests.
2. **Migration script.** `codeindexer migrate-vector --from milvus --to sqlite_vec` — re-encodes nothing; just copies vectors + metadata across.
3. **Default-flip** in a minor version after CI has been running both for a release cycle.
4. **Remove Milvus** after one more release.

### Success criteria
- On a 10K-element index, `sqlite-vec` brute-force search returns results with the same top-1 element as Milvus for a fixed query set.
- Disk footprint under 100 MB for the same index.
- p95 search latency under 200 ms on a laptop CPU.

### Risks
- **ANN index quality.** sqlite-vec's HNSW is newer than Milvus's; we may need to start with brute-force and add HNSW once we have confidence.
- **Concurrent writers.** SQLite's WAL mode is fine for our single-writer pattern but worth verifying under the watcher's burst writes.

---

## 3. Public eval harness

### Why
We claim "better than baseline retrieval." Without numbers, that's marketing. With numbers, we can iterate against a metric instead of vibes — and demote changes that look good but tank precision.

### Benchmark choice
- **CodeSearchNet** — natural-language → code retrieval, MRR/NDCG, six languages. Old but well-understood.
- **RepoBench-R** — repo-scale retrieval, more realistic than CodeSearchNet.
- **SWE-bench (retrieval slice)** — given an issue, retrieve the file that needs to change. Closer to how the tool is actually used.

Start with CodeSearchNet for the quick feedback loop; add RepoBench-R for the headline number.

### Implementation
```
eval/
    __init__.py
    csn_runner.py       # downloads/loads CodeSearchNet, runs CodeIndexer, reports MRR/NDCG
    repobench_runner.py
    baselines/
        ripgrep.py
        bm25_only.py
        vector_only.py
    report.py           # writes results/<date>-<repo>-<config>.json
```

CLI:
```
codeindexer eval csn --languages python,java --top-k 10
codeindexer eval repobench --split easy
```

### Success criteria
- Reproducible numbers checked into a results dir on each release.
- A `make eval` target a contributor can run locally in under 10 minutes for the smoke suite.
- A regression CI check that fails when MRR drops > 2 points vs. the previous release.

### Risks
- **Dataset size.** CodeSearchNet's full corpus is large. Ship a slim "smoke" subset for CI and the full thing for release evals.
- **Confounding the index.** Eval should run on a clean index per benchmark, not the user's working index.

---

## Cross-cutting: telemetry & reproducibility

Once any of the above lands, fold the numbers into `/api/metrics` so the stats are visible to the user, not just to maintainers. The latency budget framework added in v0.2.1 already has the plumbing for this.
