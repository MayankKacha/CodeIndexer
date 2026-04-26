# 🔍 CodeIndexer — Code Intelligence Platform

> **Graph + Hybrid Search + Re-ranking + Query-Aware Compression + Caching**

CodeIndexer parses codebases into atomic function/method/class-level chunks, stores them in a **graph database** (NetworkX in-memory by default, Neo4j optional) and a **vector database** (Milvus Lite), and provides **hybrid search** combining BM25 keyword matching, semantic vector search, and graph traversal — enhanced with **cross-encoder re-ranking** and **query-aware LLM compression** to reduce token usage for AI assistants. It ships with a FastAPI server consumed by the bundled VS Code extension over MCP.

## ✨ Key Features

| Feature | Description |
|---|---|
| 🌳 **Multi-Language Parsing** | Tree-sitter based parsing for 15+ languages (Python, JS, TS, Java, Go, Rust, C/C++, C#, Ruby, PHP, Kotlin, Swift, Scala, Lua) |
| 🧬 **Method-Level Splitting** | Splits code into atomic chunks at the function/method/class level with full metadata |
| 🕸️ **Graph Database** | Neo4j stores code relationships: calls, imports, inheritance, containment |
| 🧠 **Vector Embeddings** | CodeBERT (768-dim) embeddings stored in Milvus for semantic search |
| 🔎 **Hybrid Search** | Combined BM25 + Vector + Graph search with Reciprocal Rank Fusion |
| 🎯 **Cross-Encoder Re-ranking** | Re-ranks candidates for significantly improved precision |
| 🗜️ **Query-Aware Compression** | OpenAI-powered context compression — 60-80% token reduction |
| 💾 **Caching Layer** | Disk-backed cache for search results, embeddings, and compressions |
| 📝 **LLM Descriptions** | Auto-generated natural language descriptions for every code element |
| 📍 **Line Number Tracking** | Start/end line numbers for every indexed element |
| 🐙 **GitHub Support** | Clone and index any GitHub repository by URL |
| 👁️ **File Watching** | Watch directories for changes and auto-update indexes |
| 🌐 **REST API** | FastAPI server with full OpenAPI documentation |
| ⌨️ **CLI** | Rich terminal interface with beautiful output |

## 🚀 Quick Start

### 1. Install

```bash
# Clone the repository
git clone <this-repo>
cd CodeIndexer

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with all dependencies
pip install -e .
```



## 2. Class & Module Reference

### Pipeline Layer

| Class | File | Description |
|-------|------|-------------|
| `CodeIndexerPipeline` | `pipeline/indexer.py` | Main orchestrator. Manages the full indexing workflow (clone → parse → embed → store) and search pipeline. Supports incremental indexing with file hash tracking. |
| `clone_repository()` | `pipeline/git_cloner.py` | Clones GitHub repos via GitPython. Supports shallow clones and caching already-cloned directories. |
| `FileWatcher` | `pipeline/file_watcher.py` | Uses `watchdog` to monitor a directory and trigger re-indexing on file changes. |

### Parsing Layer

| Class | File | Description |
|-------|------|-------------|
| `ASTParser` | `parsing/ast_parser.py` | Tree-sitter based parser. Extracts functions, methods, classes, and modules from Python, JS, TS, Java, Go, Rust, C++. |
| `split_codebase()` | `parsing/code_splitter.py` | Walks a directory, detects languages, and splits all files into `CodeElement` instances. |
| `detect_language()` | `parsing/language_detector.py` | Maps file extensions to language names for tree-sitter. |
| `CodeElement` | `parsing/models.py` | Core data model. Holds code, metadata (file path, line range), relationships (calls, imports), and serialization methods. |
| `IndexingStats` | `parsing/models.py` | Statistics dataclass with per-phase timing (parse, embed, graph, vector) and incremental indexing status. |

### Search Layer

| Class | File | Description |
|-------|------|-------------|
| `HybridSearchEngine` | `search/hybrid_search.py` | Combines BM25, vector, and graph search results using **Reciprocal Rank Fusion (RRF)**. |
| `BM25Index` | `search/bm25_index.py` | Keyword search using `rank_bm25`. Persists to disk via pickle. |
| `Reranker` | `search/reranker.py` | Cross-encoder (`ms-marco-MiniLM-L-6-v2`) for neural re-ranking of search candidates. |
| `reciprocal_rank_fusion()` | `search/hybrid_search.py` | Merges multiple ranked lists into one using RRF scoring. |

### Vector Layer

| Class | File | Description |
|-------|------|-------------|
| `CodeEncoder` | `vectors/encoder.py` | SentenceTransformer encoder (`st-codesearch-distilroberta-base`). Generates 768-dim code embeddings. |
| `MilvusStore` | `vectors/milvus_store.py` | Milvus Lite (embedded) vector database. Stores and searches code embeddings with metadata filtering. |

### Graph Layer

| Class | File | Description |
|-------|------|-------------|
| `NetworkxStore` | `graph/networkx_store.py` | In-memory graph database using NetworkX. Stores code elements as nodes and call relationships as edges. Persists to disk. |
| `GraphQueriesNetworkx` | `graph/graph_queries_networkx.py` | Graph traversal queries: find callers, callees, call chains, impact analysis, dead code detection. |
| `Neo4jStore` | `graph/neo4j_store.py` | Neo4j backend (optional). Production-grade graph database for large codebases. |

### Intelligence Layer

| Class | File | Description |
|-------|------|-------------|
| `QueryCompressor` | `compression/compressor.py` | LLM-powered context compression. Strategies: extractive, summary, hybrid. Reduces token usage by 80%+. |
| `DescriptionGenerator` | `enrichment/description_generator.py` | Generates natural language descriptions for code elements using OpenAI. |
| `CodeAssistant` | `rag/agent.py` | RAG agent that retrieves code context, compresses it, and streams answers via OpenAI GPT-4o. |

### Infrastructure Layer

| Class | File | Description |
|-------|------|-------------|
| `CacheManager` | `cache/cache_manager.py` | Disk-backed cache using `diskcache`. Stores search results, embeddings, compressions, file hashes, and repo metadata. |
| `Settings` | `config/settings.py` | Pydantic-based configuration. Loads from `.env` file. Controls all model names, weights, paths, and API keys. |

### API Layer

| Class | File | Description |
|-------|------|-------------|
| `FastAPI app` | `api/server.py` | REST API with SSE indexing progress, streaming chat, analytics, and repository management endpoints. Used by the VS Code extension over MCP. |

---



### 3. Configure

```bash
# Copy the environment template
cp .env.example .env

# Edit .env with your credentials:
# - OPENAI_API_KEY (required for descriptions & compression)
# - NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
# - MILVUS_URI (default: ./milvus_code.db for local Milvus Lite)
```

### 4. Index a Codebase

```bash
# Index a local directory
codeindexer index /path/to/your/project

# Index a GitHub repository
codeindexer index https://github.com/user/repo

# Index without LLM descriptions (faster, no API key needed)
codeindexer index /path/to/project --no-desc
```

### 5. Search & Ask

```bash
# Hybrid search (BM25 + Vector + Graph with re-ranking)
codeindexer search "payment processing logic"

# Search with compression for minimal LLM context
codeindexer search "user authentication" --compress

# Ask the RAG Assistant a question about the code
codeindexer ask "How are embeddings stored?"

# Get code change recommendations from the RAG Assistant
codeindexer recommend "Change the default encoder model from codebert to unixcoder"

# Find exact function name
codeindexer find processPayment

# Output as JSON
codeindexer search "database connection" --json
```

### 6. Graph Analysis

```bash
# Find who calls a function
codeindexer callers processPayment

# Find what a function calls
codeindexer callees initializeApp

# Impact analysis
codeindexer impact validateInput

# List indexed repositories
codeindexer list
```

### 7. API Server

```bash
# Start the REST API
codeindexer serve

# API docs at http://localhost:8000/docs
```


## 8. Configuration Reference

All settings are in `.env` or environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for descriptions, compression, and chat |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for descriptions & compression |
| `OPENAI_CHAT_MODEL` | `gpt-4o` | Model for RAG chat |
| `GRAPH_BACKEND` | `networkx` | Graph database: `networkx` or `neo4j` |
| `ENCODER_MODEL` | `st-codesearch-distilroberta-base` | Code embedding model |
| `BM25_WEIGHT` | `0.4` | Weight for BM25 results in hybrid fusion |
| `VECTOR_WEIGHT` | `0.6` | Weight for vector search in hybrid fusion |
| `COMPRESSION_STRATEGY` | `hybrid` | Compression mode: `extractive`, `summary`, `hybrid` |
| `CACHE_TTL` | `3600` | Cache expiration in seconds |
| `API_PORT` | `8000` | FastAPI port |


## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Input Layer                       │
│  Local Directory ──┐                                │
│  GitHub URL ───────┼──▶ Code Parser (tree-sitter)   │
└────────────────────┴────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│              Parsing Layer (15+ languages)           │
│  Language Detect → AST Parse → Code Split           │
│  Classes → Methods → Functions (method-level)       │
│  + Line numbers, params, calls, imports, complexity │
└─────────────────────────────────────────────────────┘
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
┌────────────────┐ ┌───────────┐ ┌──────────────┐
│ Neo4j Graph DB │ │  Milvus   │ │  BM25 Index  │
│  (Nodes +      │ │ (CodeBERT │ │ (Keyword     │
│   Edges)       │ │  Vectors) │ │  Search)     │
└────────────────┘ └───────────┘ └──────────────┘
            │             │             │
            └─────────────┼─────────────┘
                          ▼
┌─────────────────────────────────────────────────────┐
│              Search Pipeline                         │
│  Query → BM25 + Vector + Graph                      │
│       → Reciprocal Rank Fusion (RRF)                │
│       → Cross-Encoder Re-ranking                    │
│       → Query-Aware Compression (OpenAI)            │
│       → Cache                                       │
└─────────────────────────────────────────────────────┘
```

## 📊 Vector Metadata Schema

Every code element is stored with rich metadata:

```
Element ID:     a1b2c3d4e5f6g7h8
Type:           function
Name:           processPayment
Qualified Name: PaymentService.processPayment
File:           payments/service.py
Lines:          45-62
Language:       python
Description:    Validates user payment inputs, checks balance, initiates transaction
Signature:      def processPayment(self, user: User, amount: Decimal) -> PaymentResult:
Code:           def processPayment(self, user, amount): ...
Parent Class:   PaymentService
Complexity:     8
```

## 🔧 CLI Commands

| Command | Description |
|---|---|
| `codeindexer index <path>` | Index a local directory or GitHub URL |
| `codeindexer search "<query>"` | Hybrid search with re-ranking |
| `codeindexer ask "<query>"` | Ask the RAG assistant a question |
| `codeindexer recommend "<req>"`| Get code change recommendations |
| `codeindexer find <name>` | Find exact code element by name |
| `codeindexer callers <name>` | Find all callers of a function |
| `codeindexer callees <name>` | Find all called functions |
| `codeindexer impact <name>` | Impact analysis for a function |
| `codeindexer list` | List indexed repositories |
| `codeindexer delete <name>` | Delete a repository from indexes |
| `codeindexer stats` | Show system statistics |
| `codeindexer serve` | Start the REST API server |
| `codeindexer watch <path>` | Watch directory for changes |

## 🌐 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/index` | Index a codebase |
| `POST` | `/search` | Hybrid search |
| `POST` | `/search/compressed` | Search + compression |
| `POST` | `/rag/ask` | Ask a question (Streamed) |
| `POST` | `/rag/recommend` | Code recommendation (Streamed) |
| `POST` | `/graph/callers` | Find callers |
| `POST` | `/graph/callees` | Find callees |
| `POST` | `/graph/call-chain` | Find call chain |
| `POST` | `/graph/impact` | Impact analysis |
| `GET` | `/graph/dead-code` | Find dead code |
| `GET` | `/repositories` | List repositories |
| `DELETE` | `/repositories/{name}` | Delete repository |
| `GET` | `/stats` | System statistics |

## 🤝 Supported Languages

Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C#, Ruby, PHP, Kotlin, Swift, Scala, Lua, Bash

## 📦 Dependencies

- **Parsing**: tree-sitter, tree-sitter-language-pack
- **Graph**: networkx (default, in-memory). Neo4j is optional: `pip install -e ".[neo4j]"`
- **Vectors**: pymilvus, transformers (CodeBERT), torch
- **Search**: rank-bm25, sentence-transformers (CrossEncoder)
- **LLM**: openai, tiktoken
- **API**: fastapi, uvicorn
- **CLI**: typer, rich
- **Utilities**: gitpython, watchdog, diskcache, pydantic

## 📄 License

 MIT License
