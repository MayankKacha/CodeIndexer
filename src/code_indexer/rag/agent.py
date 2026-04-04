"""
RAG Agent that uses the CodeIndexer pipeline as a retrieval engine.

The agent takes user questions or requirements, searches the indexed
codebase, compresses the context to save tokens, and uses an LLM
to provide intelligent answers or code change recommendations.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, Generator, List, Optional, Union

from openai import OpenAI

from code_indexer.pipeline.indexer import CodeIndexerPipeline

logger = logging.getLogger(__name__)


class CodeAssistant:
    """RAG-powered coding assistant."""

    def __init__(self, pipeline: CodeIndexerPipeline, api_key: str, model: str = "gpt-4o"):
        """Initialize the assistant.

        Args:
            pipeline: Initialized CodeIndexerPipeline for retrieval.
            api_key: OpenAI API key.
            model: OpenAI chat model to use for reasoning.
        """
        self.pipeline = pipeline
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _get_context(self, query: str, repo_name: str = "") -> str:
        """Retrieve and compress context for a query."""
        logger.info(f"Retrieving context for query: '{query}'")
        
        # We fetch top 15 candidates before compression to ensure good coverage
        result = self.pipeline.search(
            query=query,
            top_k=15,
            repo_name=repo_name,
            use_reranker=True,
            use_compression=True,
        )

        compression = result.get("compression", {})
        context = compression.get("compressed_context", "")
        
        if not context:
            # Fallback if compression is disabled or failed
            results = result.get("results", [])
            context = "\n\n".join([
                f"File: {r.get('file_path', 'unknown')} | Lines: {r.get('start_line', '?')}-{r.get('end_line', '?')}\n"
                f"Element: {r.get('qualified_name', r.get('name', 'unknown'))}\n"
                f"```python\n{r.get('code', '')}\n```"
                for r in results[:5] 
            ])

        return context

    def ask_stream(self, query: str, repo_name: str = "") -> Generator[str, None, None]:
        """Ask a question and stream the response back.

        Args:
            query: User's question about the codebase.
            repo_name: Optional filter for a specific repository.

        Yields:
            Chunks of the LLM response.
        """
        context = self._get_context(query, repo_name)

        if not context.strip():
            yield "I couldn't find any relevant code to answer your question."
            return

        system_prompt = (
            "You are an expert software engineer and architect. "
            "Your task is to answer questions about a codebase using the provided context. "
            "The context contains highly compressed snippets from the codebase that match the user's query. "
            "Always cite the files and methods you reference. "
            "If the context does not contain enough information to answer the question securely, say so. "
            "Keep your explanations clear, concise, and focused on the code."
        )

        user_prompt = f"Context:\n{context}\n\nQuestion:\n{query}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                temperature=0.2, # Low temperature for more factual answers
            )

            for chunk in response:
                content = chunk.choices[0].delta.content
                if content is not None:
                    yield content

        except Exception as e:
            logger.error(f"Error during ask_stream: {e}")
            yield f"\n\n[Error communicating with LLM: {e}]"

    def recommend_stream(self, requirement: str, repo_name: str = "") -> Generator[str, None, None]:
        """Recommend code changes based on a requirement and stream the response.

        Args:
            requirement: User's requirement (e.g., "Add Redis caching to the search function").
            repo_name: Optional filter for a specific repository.

        Yields:
            Chunks of the LLM response containing the technical plan and code snippets.
        """
        context = self._get_context(requirement, repo_name)

        if not context.strip():
            yield "I couldn't find any relevant code contexts to attach to this requirement."
            return

        system_prompt = (
            "You are a Staff Software Engineer pairing with another developer. "
            "You have been given a feature requirement or a bug fix to implement. "
            "You are also given a context containing existing code snippets relevant to the requirement. "
            "Your task is to provide a concrete, step-by-step implementation plan. "
            "Format your response as follows:\n"
            "1. **Analysis**: Briefly explain what needs to change based on the provided existing code.\n"
            "2. **Implementation Plan**: Bullet points of files to touch and what to change.\n"
            "3. **Code Changes**: Provide actual code snippets showing the modifications or new code. "
            "Use diff-like format if modifying existing code, or just the new code block.\n"
            "Be precise and ensure your code integrates perfectly with the given context."
        )

        user_prompt = f"Existing Code Context:\n{context}\n\nRequirement:\n{requirement}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                temperature=0.4, # Slightly higher temperature for creative problem solving
            )

            for chunk in response:
                content = chunk.choices[0].delta.content
                if content is not None:
                    yield content

        except Exception as e:
            logger.error(f"Error during recommend_stream: {e}")
            yield f"\n\n[Error communicating with LLM: {e}]"
