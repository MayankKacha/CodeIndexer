"""
Evaluation metrics for CodeIndexer benchmarking.

Includes token counting via tiktoken and an LLM-as-a-judge implementation
to score context relevance to a given query.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import tiktoken
from openai import OpenAI

from code_indexer.config.settings import get_settings

logger = logging.getLogger(__name__)


def count_tokens(text: str, model_name: str = "gpt-4o-mini") -> int:
    """Count the number of tokens in a text string."""
    try:
        encoding = tiktoken.encoding_for_model(model_name)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


class RelevanceEvaluator:
    """LLM-as-a-judge evaluator for measuring context relevance."""

    def __init__(self, api_key: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.openai_api_key
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            self.model = settings.openai_model
        else:
            self.client = None

    def evaluate_relevance(self, query: str, context: str) -> float:
        """
        Score how relevant the provided context is to answering the query.
        Returns a score from 1.0 to 10.0.
        """
        if not self.client:
            logger.warning("No OpenAI API key provided. Skipping relevance evaluation.")
            return 0.0

        if not context.strip():
            logger.warning("Empty context passed to evaluator, returning 0.")
            return 0.0

        # Trim context to 40000 chars (approx 10000 tokens) to stay within limits
        context_trimmed = context[:40000]

        system_prompt = (
            "You are a code retrieval quality judge. "
            "Given a user's question and retrieved code context, rate the relevance from 1-10.\n"
            "Scale: 1=completely irrelevant, 4=slightly relevant, 7=mostly answers the question, 10=perfectly answers it.\n"
            "Respond with ONLY a number (integer or one decimal place). No other text."
        )
        user_prompt = (
            f"Question: {query}\n\n"
            f"Retrieved Code:\n{context_trimmed}\n\n"
            f"Score (1-10):"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=10,
            )
            raw = response.choices[0].message.content.strip()
            logger.info(f"[RelevanceEvaluator] LLM raw output: '{raw}' for query: '{query[:60]}'")

            import re
            # Extract the first float-like or int-like number from the output
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw)
            if not match:
                logger.error(f"[RelevanceEvaluator] Could not parse numeric score from: '{raw}'")
                return 1.0

            score = float(match.group(1))
            
            # If output was '1' out of 10, it matches 1. If '8.5/10', matches 8.5.
            # Clamp to valid range
            return min(max(score, 1.0), 10.0)

        except Exception as e:
            logger.error(f"[RelevanceEvaluator] API error: {e}")
            return 0.0
