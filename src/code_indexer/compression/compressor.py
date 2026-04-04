"""
Query-aware context compression using OpenAI.

Reduces LLM token usage by compressing search results to only the
information relevant to the user's query. Supports extractive,
summary, and hybrid compression strategies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QueryCompressor:
    """Compress code context based on query relevance using OpenAI."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        strategy: str = "hybrid",
        max_tokens: int = 2000,
    ):
        """Initialize the compressor.

        Args:
            api_key: OpenAI API key.
            model: OpenAI model to use.
            strategy: Compression strategy (extractive, summary, hybrid).
            max_tokens: Maximum tokens in compressed output.
        """
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.strategy = strategy
        self.max_tokens = max_tokens

    def compress(
        self,
        query: str,
        results: List[Dict[str, Any]],
        max_results: int = 10,
    ) -> Dict[str, Any]:
        """Compress search results based on query relevance.

        Args:
            query: The user's search query.
            results: Search results with code and metadata.
            max_results: Maximum results to include.

        Returns:
            Dict with compressed_context, token_stats, and elements.
        """
        if not results:
            return {
                "compressed_context": "",
                "original_tokens": 0,
                "compressed_tokens": 0,
                "compression_ratio": 0.0,
                "elements": [],
            }

        results = results[:max_results]

        # Build the original context
        original_context = self._build_original_context(results)
        original_tokens = self._estimate_tokens(original_context)

        if self.strategy == "extractive":
            compressed = self._compress_extractive(query, results)
        elif self.strategy == "summary":
            compressed = self._compress_summary(query, results)
        else:  # hybrid
            compressed = self._compress_hybrid(query, results)

        compressed_tokens = self._estimate_tokens(compressed)

        ratio = (1 - compressed_tokens / max(original_tokens, 1)) * 100

        logger.info(
            f"Compressed {original_tokens} → {compressed_tokens} tokens "
            f"({ratio:.1f}% reduction)"
        )

        return {
            "compressed_context": compressed,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "compression_ratio": round(ratio, 1),
            "elements": [
                {
                    "name": r.get("name", ""),
                    "qualified_name": r.get("qualified_name", ""),
                    "file_path": r.get("file_path", ""),
                    "start_line": r.get("start_line", 0),
                    "end_line": r.get("end_line", 0),
                    "element_type": r.get("element_type", ""),
                }
                for r in results
            ],
        }

    def _build_original_context(self, results: List[Dict]) -> str:
        """Build the full uncompressed context string."""
        parts = []
        for r in results:
            parts.append(f"{'─' * 60}")
            parts.append(f"  {r.get('element_type', 'code').title()}: {r.get('qualified_name', r.get('name', ''))}")
            parts.append(f"  File: {r.get('file_path', '')}  (Lines {r.get('start_line', '?')}–{r.get('end_line', '?')})")
            if r.get("description"):
                parts.append(f"  Description: {r['description']}")
            parts.append(f"{'─' * 60}")
            parts.append(r.get("code", ""))
            parts.append("")
        return "\n".join(parts)

    def _compress_extractive(self, query: str, results: List[Dict]) -> str:
        """Keep only the most relevant lines from each code element."""
        prompt = f"""You are a code context compressor. Given a query and code search results,
extract ONLY the lines of code most relevant to the query. Keep function signatures,
key logic, and comments. Remove boilerplate, imports, and irrelevant implementation details.

Format each result as:
## [ElementType]: [Name]
File: [file_path] (Lines [start]-[end])
```
[relevant code lines only]
```

Query: {query}

Code Results:
{self._build_original_context(results)}

Extract only the most query-relevant lines. Be concise but preserve accuracy."""

        return self._call_openai(prompt)

    def _compress_summary(self, query: str, results: List[Dict]) -> str:
        """Generate concise summaries of each code element."""
        prompt = f"""You are a code context compressor. Given a query and code search results,
create a concise summary of each code element that captures its purpose and relevance
to the query. Include function signatures but summarize the implementation.

Format each result as:
## [ElementType]: [QualifiedName]
File: [file_path] (Lines [start]-[end])
Signature: [function/method signature]
Summary: [1-2 sentence summary focused on query relevance]

Query: {query}

Code Results:
{self._build_original_context(results)}

Create concise, accurate summaries focused on query relevance."""

        return self._call_openai(prompt)

    def _compress_hybrid(self, query: str, results: List[Dict]) -> str:
        """Combine summaries with key code lines."""
        prompt = f"""You are a code context compressor. Given a query and code search results,
create a compressed view that includes:
1. Function/method signature
2. A brief 1-line description of what it does relevant to the query
3. Only the KEY lines of code that are most relevant to the query (not the full body)
4. File location with line numbers

Format each result as:
## [ElementType]: [QualifiedName]
📍 File: [file_path] | Lines: [start]-[end]
📝 [Brief description relevant to query]
```[language]
[signature]
[... only key relevant lines ...]
```

Query: {query}

Code Results:
{self._build_original_context(results)}

Create compressed but accurate context. Preserve code correctness."""

        return self._call_openai(prompt)

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API and return the response."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a code context compressor. Output only the compressed context, no explanations.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI compression call failed: {e}")
            # Fallback: return truncated original
            return prompt[:self.max_tokens * 4]  # Rough char estimate

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using tiktoken."""
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.model)
            return len(enc.encode(text))
        except Exception:
            # Rough estimate: ~4 chars per token
            return len(text) // 4
