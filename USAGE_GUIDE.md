# 📖 CodeIndexer Usage Guide

This guide provides a step-by-step walkthrough to get you from zero to a fully indexed, searchable, and benchmarked codebase using CodeIndexer.

---

## 🛠️ Step 1: Installation

First, ensure you have **Python 3.10+** installed.

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd CodeIndexer
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install the package in editable mode:**
   ```bash
   pip install -e .
   ```
   > [!NOTE]
   > This installs all dependencies including Tree-sitter, Milvus Lite, Neo4j drivers, and OpenAI clients.

---

## ⚙️ Step 2: Configuration

CodeIndexer requires an OpenAI API key for generating code descriptions and performing query-aware compression.

1. **Create your `.env` file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and add your OpenAI API Key:**
   ```env
   OPENAI_API_KEY=sk-your-key-here
   ```

3. **Database Setup (Optional for basic use):**
   - **Milvus:** By default, it uses `milvus_lite` (a local file `milvus_code.db`), so no setup is needed.
   - **Neo4j:** If you want graph features, ensure a Neo4j instance is running and update `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD`.

---

## 📥 Step 3: Indexing a Codebase

Before you can search, you must index a project. You can index a local directory or a GitHub URL.

### Local Directory
```bash
codeindexer index /path/to/your/repo --name my-project
```

### GitHub Repository
```bash
codeindexer index https://github.com/fastapi/fastapi --name fastapi
```

> [!TIP]
> If you are in a hurry or don't want to use OpenAI credits, add `--no-desc` to skip LLM-generated descriptions.

---

## 🔎 Step 4: Searching the Code

Once indexed, you can perform high-performance hybrid searches.

1. **Basic Search:**
   ```bash
   codeindexer search "how is authentication handled?"
   ```

2. **Search with LLM Compression:**
   This produces a highly condensed version of the code snippets, perfect for pasting into an LLM prompt.
   ```bash
   codeindexer search "database schema" --compress
   ```

3. **Find exact definitions:**
   ```bash
   codeindexer find MyClassName
   ```

---

## 🤖 Step 5: Using the RAG Assistant

Ask questions directly to your codebase using the built-in RAG (Retrieval-Augmented Generation) agent.

1. **Ask a question:**
   ```bash
   codeindexer ask "Explain the logic in the payment gateway"
   ```

2. **Get implementation recommendations:**
   ```bash
   codeindexer recommend "Add a new endpoint for user profile updates"
   ```

---

## 📊 Step 6: Running Benchmarks (UI)

CodeIndexer includes a powerful Streamlit dashboard to compare different retrieval architectures:

1. **Launch the Dashboard:**
   ```bash
   codeindexer ui
   ```

2. **Interact:**
   - Open [http://localhost:8501](http://localhost:8501) in your browser.
   - Select your indexed repository.
   - Enter a query in the chat box.
   - **Observe the charts:** Compare token usage (Cost) and Relevance Scores (Quality).

---

## 💥 Step 7: Graph & Impact Analysis

Leverage the power of the Neo4j graph to understand code relationships.

1. **Find Callers:** See who calls a specific function.
   ```bash
   codeindexer callers update_user_balance
   ```

2. **Impact Analysis:** See what parts of the system might break if you change a function.
   ```bash
   codeindexer impact BaseEngine.connect
   ```

---

## 🌐 Step 8: Starting the API Server

If you want to integrate CodeIndexer into your own applications:

1. **Start the FastAPI server:**
   ```bash
   codeindexer serve
   ```

2. **Access Documentation:**
   Open [http://localhost:8000/docs](http://localhost:8000/docs) to see the interactive Swagger UI.

---

## 📋 Summary of CLI Commands

| Command | Purpose |
|---|---|
| `index` | Ingest code from disk or GitHub |
| `search` | Multi-stage hybrid retrieval |
| `ask` | Conversational interface for code |
| `recommend` | AI-driven feature implementation plans |
| `ui` | Visual benchmarking & dashboard |
| `callers/callees` | Navigate the call graph |
| `impact` | Change propagation analysis |
| `list` | Show all indexed projects |
| `stats` | View database & index health |
