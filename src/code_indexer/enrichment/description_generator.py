"""
LLM-powered description generator for code elements.

Uses OpenAI to generate natural language descriptions for functions,
methods, and classes to improve search quality and context for LLMs.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)


class DescriptionGenerator:
    """Generate natural language descriptions for code elements using OpenAI."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        batch_size: int = 10,
        rate_limit_delay: float = 0.5,
    ):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.batch_size = batch_size
        self.rate_limit_delay = rate_limit_delay

    def generate_description(self, element: CodeElement) -> str:
        """Generate a description for a single code element.

        Args:
            element: The code element to describe.

        Returns:
            Natural language description string.
        """
        prompt = self._build_prompt(element)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a code documentation expert. Generate a concise, "
                            "informative description of the given code element. "
                            "Focus on what it does, its purpose, and key behaviors. "
                            "Keep the description to 1-2 sentences. "
                            "Do NOT include the function name in the description."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=150,
                temperature=0.2,
            )
            description = response.choices[0].message.content.strip()
            return description
        except Exception as e:
            logger.warning(f"Failed to generate description for {element.name}: {e}")
            # Fallback: use docstring or signature
            return element.docstring or f"A {element.element_type} named {element.name}"

    def generate_descriptions_batch(
        self,
        elements: List[CodeElement],
        show_progress: bool = True,
    ) -> List[CodeElement]:
        """Generate descriptions for multiple code elements.

        Updates each element's description field in-place and returns
        the modified list.

        Args:
            elements: Code elements needing descriptions.
            show_progress: Whether to show a progress bar.

        Returns:
            The same elements list with descriptions populated.
        """
        elements_needing_desc = [
            el for el in elements
            if not el.description and el.element_type in ("function", "method", "class")
        ]

        if not elements_needing_desc:
            logger.info("All elements already have descriptions")
            return elements

        logger.info(f"Generating descriptions for {len(elements_needing_desc)} elements")

        if show_progress:
            try:
                from rich.progress import track
                iterator = track(
                    elements_needing_desc,
                    description="Generating descriptions...",
                )
            except ImportError:
                iterator = elements_needing_desc
        else:
            iterator = elements_needing_desc

        for element in iterator:
            # Skip if element already has a good docstring
            if element.docstring and len(element.docstring) > 20:
                element.description = element.docstring
                continue

            element.description = self.generate_description(element)

            # Rate limiting
            time.sleep(self.rate_limit_delay)

        described_count = sum(1 for el in elements if el.description)
        logger.info(f"Generated descriptions for {described_count}/{len(elements)} elements")

        return elements

    def _build_prompt(self, element: CodeElement) -> str:
        """Build the prompt for description generation."""
        parts = [
            f"Language: {element.language}",
            f"Type: {element.element_type}",
            f"Name: {element.qualified_name or element.name}",
            f"File: {element.file_path}",
        ]

        if element.signature:
            parts.append(f"Signature: {element.signature}")

        if element.parameters:
            parts.append(f"Parameters: {', '.join(element.parameters)}")

        if element.return_type:
            parts.append(f"Returns: {element.return_type}")

        if element.docstring:
            parts.append(f"Existing docstring: {element.docstring[:200]}")

        if element.code:
            code = element.code
            if len(code) > 1500:
                code = code[:1500] + "\n... (truncated)"
            parts.append(f"\nCode:\n{code}")

        return "\n".join(parts)
