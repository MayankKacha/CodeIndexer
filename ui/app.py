"""
Streamlit Web Interface for CodeIndexer and Benchmarking.

Provides an interactive GUI to:
1. View currently indexed codebase.
2. Ask questions or request code changes (chat UI).
3. Run benchmarks comparing:
    - Baseline (BM25 or simple Vector)
    - CodeGraphContext (Graph+Vector)
    - CodeIndexer (Hybrid + Rerank + Compress)
4. Visualize performance in interactive charts.
"""

import sys
from pathlib import Path

# Add src to Python path so we can import internal modules
sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

import altair as alt
import pandas as pd
import streamlit as st
import asyncio
from code_indexer.evaluator.benchmarker import Benchmarker
from code_indexer.pipeline.indexer import CodeIndexerPipeline


@st.cache_resource
def get_pipeline():
    """Cache the heavy pipeline initialization."""
    return CodeIndexerPipeline()


def get_benchmarker(pipeline):
    """Always create a fresh Benchmarker to pick up evaluator config changes."""
    return Benchmarker(pipeline)


def main():
    st.set_page_config(
        page_title="CodeIndexer Evaluator",
        page_icon="🔍",
        layout="wide"
    )

    st.title("🔍 CodeIndexer Evaluation & Benchmarking")

    # Access backend
    pipeline = get_pipeline()
    benchmarker = get_benchmarker(pipeline)

    # ── Sidebar ──
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Determine available repos
        repos = pipeline.list_repositories()
        repo_names = [r["name"] for r in repos] if repos else ["(No Repositories Indexed)"]
        
        selected_repo = st.selectbox("Indexed Repository", repo_names)
        
        if st.button("🔄 Refresh Data"):
            st.rerun()

        st.markdown("---")
        st.subheader("System Stats")
        stats = pipeline.get_stats()
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Elements", stats.get("graph", {}).get("total_elements", 0))
            st.metric("Classes", stats.get("graph", {}).get("classes", 0))
        with col2:
            st.metric("Functions", stats.get("graph", {}).get("functions", 0))
            st.metric("Methods", stats.get("graph", {}).get("methods", 0))
            
    # ── Main Chat Interface ──
    st.markdown("### Ask the LLM or Recommend Changes")
    
    # Session state for chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("E.g. 'How does Reciprocal Rank Fusion work?' or 'Recommend me how to add Redis caching'"):
        # Display user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Run benchmarks before generating the main stream
        if not repos or repo_names[0] == "(No Repositories Indexed)":
            st.error("Please index a repository using the CLI first: `codeindexer index <path>`")
            return

        with st.status("🔍 Running Benchmark across Architectures...", expanded=True) as status:
            st.write("Evaluating Naive Baseline...")
            st.write("Evaluating Simulated CodeGraphContext...")
            st.write("Evaluating Real CodeGraphContext (CGC Engine)...")
            st.write("Evaluating CodeIndexer (Hybrid+Compress)...")
            
            # Run background evaluator
            target_repo = selected_repo if selected_repo != "(All Repositories)" else ""
            benchmark_results = asyncio.run(benchmarker.run_benchmark(prompt, repo_name=target_repo))
            status.update(label="✅ All Benchmarks Complete!", state="complete", expanded=False)

        # ── Display Benchmark Charts ──
        st.markdown("### 📊 Retrieval Performance Benchmark")
        
        # Prepare Dataframe for logging
        df_data = []
        for arch, res in benchmark_results.items():
            df_data.append({
                "Architecture": res.architecture,
                "Latency (ms)": round(res.latency_ms, 2),
                "Tokens Used": res.token_count,
                "Quality / Relevance (0-10)": round(res.relevance_score, 1)
            })
            
        df = pd.DataFrame(df_data)

        # Build Charts
        col1, col2 = st.columns(2)
        
        with col1:
            # Token Chart (Lower is better)
            token_chart = alt.Chart(df).mark_bar().encode(
                x=alt.X('Tokens Used:Q', title='LLM Context Window Tokens (Lower is Better)'),
                y=alt.Y('Architecture:N', sort="-x", title=None),
                color=alt.Color('Architecture:N', legend=None),
                tooltip=['Architecture', 'Tokens Used']
            ).properties(title="Context Window Bloat")
            st.altair_chart(token_chart, use_container_width=True)

        with col2:
            # Quality Chart (Higher is better)
            quality_chart = alt.Chart(df).mark_bar().encode(
                x=alt.X('Quality / Relevance (0-10):Q', title='LLM-Graded Relevance Score (Out of 10)'),
                y=alt.Y('Architecture:N', sort="-x", title=None),
                color=alt.Color('Architecture:N', legend=None),
                tooltip=['Architecture', 'Quality / Relevance (0-10)']
            ).properties(title="Context Relevance")
            st.altair_chart(quality_chart, use_container_width=True)

        with st.expander("📋 Context Quality Debug", expanded=False):
            for arch, res in benchmark_results.items():
                st.markdown(f"**{arch}** — {res.token_count} tokens | Relevance: {res.relevance_score}/10")
                if res.context.strip():
                    st.code(res.context[:800], language="python")
                else:
                    st.warning(f"⚠️ {arch}: Context is EMPTY — search returned no results!")
                st.markdown("---")

        with st.expander("Show Raw Data Table"):
            st.dataframe(df, use_container_width=True)

        # ── Stream LLM Response ──
        st.markdown("### 🤖 CodeIndexer Assistant Response")
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            full_response = ""
            
            try:
                # Based on prompt semantics, decide whether to ask or recommend
                if any(word in prompt.lower() for word in ["recommend", "change", "add", "fix", "implement"]):
                    generator = pipeline.rag_agent.recommend_stream(prompt, target_repo)
                else:
                    generator = pipeline.rag_agent.ask_stream(prompt, target_repo)
                    
                for chunk in generator:
                    full_response += chunk
                    message_placeholder.markdown(full_response + "▌")
                message_placeholder.markdown(full_response)
            except Exception as e:
                st.error(f"Error communicating with LLM: {e}")
                full_response = f"Error: {e}"
        
        st.session_state.messages.append({"role": "assistant", "content": full_response})

if __name__ == "__main__":
    main()
