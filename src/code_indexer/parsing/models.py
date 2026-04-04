"""
Data models for parsed code elements.

Every code element (function, method, class, module) is represented as a
CodeElement dataclass with rich metadata for indexing and search.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CodeElement:
    """A single parsed code element with full metadata."""

    # ── Identity ────────────────────────────────────────────────────────
    element_id: str = ""                # Unique identifier (auto-generated)
    element_type: str = ""              # function | method | class | module
    name: str = ""                      # e.g. "processPayment"
    qualified_name: str = ""            # e.g. "PaymentService.processPayment"

    # ── Location ────────────────────────────────────────────────────────
    file_path: str = ""                 # Relative path: "payments/service.py"
    repo_name: str = ""                 # Repository name
    language: str = ""                  # e.g. "python"
    start_line: int = 0                 # Line number where element starts
    end_line: int = 0                   # Line number where element ends

    # ── Code ────────────────────────────────────────────────────────────
    code: str = ""                      # Full source code of the element
    signature: str = ""                 # Function/method signature line
    docstring: str = ""                 # Extracted docstring / comment block
    description: str = ""              # LLM-generated description

    # ── Structure ───────────────────────────────────────────────────────
    parameters: List[str] = field(default_factory=list)
    return_type: str = ""               # Return type annotation if available
    decorators: List[str] = field(default_factory=list)
    parent_class: Optional[str] = None  # Parent class (for methods)
    parent_element_id: Optional[str] = None

    # ── Relationships ───────────────────────────────────────────────────
    imports: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    inherits_from: List[str] = field(default_factory=list)

    # ── Metrics ─────────────────────────────────────────────────────────
    complexity: int = 0                 # Cyclomatic complexity estimate
    line_count: int = 0                 # Number of lines

    def __post_init__(self):
        """Generate ID and compute derived fields."""
        if not self.element_id:
            self.element_id = self._generate_id()
        if not self.qualified_name:
            if self.parent_class:
                self.qualified_name = f"{self.parent_class}.{self.name}"
            else:
                self.qualified_name = self.name
        if not self.line_count and self.start_line and self.end_line:
            self.line_count = self.end_line - self.start_line + 1

    def _generate_id(self) -> str:
        """Generate a unique deterministic ID."""
        key = f"{self.repo_name}:{self.file_path}:{self.qualified_name or self.name}:{self.start_line}"
        return hashlib.sha256(key.encode()).hexdigest()[:24]

    def to_search_text(self) -> str:
        """Build the text used for BM25 indexing and embedding.

        Combines name, description, signature, docstring, and code for
        comprehensive search coverage.
        """
        parts = []
        if self.element_type:
            parts.append(f"Type: {self.element_type}")
        if self.name:
            parts.append(f"Name: {self.name}")
        if self.qualified_name and self.qualified_name != self.name:
            parts.append(f"Qualified Name: {self.qualified_name}")
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        if self.description:
            parts.append(f"Description: {self.description}")
        if self.signature:
            parts.append(f"Signature: {self.signature}")
        if self.docstring:
            parts.append(f"Docstring: {self.docstring}")
        if self.code:
            parts.append(f"Code:\n{self.code}")
        return "\n".join(parts)

    def to_embedding_text(self) -> str:
        """Build the text used specifically for CodeBERT embedding.

        Uses a compact format optimized for the encoder's 512 token limit.
        """
        parts = [f"{self.element_type}: {self.name}"]
        if self.description:
            parts.append(f"// {self.description}")
        if self.signature:
            parts.append(self.signature)
        elif self.code:
            # Use first 10 lines if no separate signature
            lines = self.code.strip().split("\n")[:10]
            parts.append("\n".join(lines))
        return "\n".join(parts)

    def to_display_dict(self) -> dict:
        """Return a dict formatted for display in search results."""
        return {
            "element_type": self.element_type,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "description": self.description,
            "signature": self.signature,
            "code": self.code,
            "parent_class": self.parent_class,
            "complexity": self.complexity,
            "repo_name": self.repo_name,
        }

    def to_context_string(self) -> str:
        """Format as a context string for LLM consumption.

        This is the format that gets injected into LLM prompts.
        """
        lines = [
            f"{'─' * 60}",
            f"  {self.element_type.title()}: {self.qualified_name}",
            f"  File: {self.file_path}  (Lines {self.start_line}–{self.end_line})",
        ]
        if self.description:
            lines.append(f"  Description: {self.description}")
        lines.append(f"{'─' * 60}")
        lines.append(self.code)
        return "\n".join(lines)


@dataclass
class ParsedFile:
    """Result of parsing a single source file."""

    file_path: str
    language: str
    repo_name: str
    elements: List[CodeElement] = field(default_factory=list)
    raw_imports: List[str] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)

    @property
    def element_count(self) -> int:
        return len(self.elements)


@dataclass
class IndexingStats:
    """Statistics from an indexing run."""

    repo_name: str = ""
    local_repo_path: str = ""
    total_files: int = 0
    parsed_files: int = 0
    skipped_files: int = 0
    total_elements: int = 0
    functions: int = 0
    methods: int = 0
    classes: int = 0
    modules: int = 0
    total_lines: int = 0
    languages: dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    embedding_count: int = 0
    graph_nodes: int = 0
    graph_relationships: int = 0
    indexing_time_seconds: float = 0.0

    # Phase-level timing for analytics
    parse_time_seconds: float = 0.0
    embedding_time_seconds: float = 0.0
    graph_time_seconds: float = 0.0
    vector_time_seconds: float = 0.0
    description_time_seconds: float = 0.0
    bm25_time_seconds: float = 0.0

    # Incremental indexing
    files_changed: int = 0
    files_unchanged: int = 0
    is_incremental: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict for API responses."""
        return {
            "repo_name": self.repo_name,
            "local_repo_path": self.local_repo_path,
            "total_files": self.total_files,
            "parsed_files": self.parsed_files,
            "skipped_files": self.skipped_files,
            "total_elements": self.total_elements,
            "functions": self.functions,
            "methods": self.methods,
            "classes": self.classes,
            "modules": self.modules,
            "total_lines": self.total_lines,
            "languages": self.languages,
            "errors": self.errors,
            "embedding_count": self.embedding_count,
            "graph_nodes": self.graph_nodes,
            "graph_relationships": self.graph_relationships,
            "indexing_time_seconds": self.indexing_time_seconds,
            "parse_time_seconds": self.parse_time_seconds,
            "embedding_time_seconds": self.embedding_time_seconds,
            "graph_time_seconds": self.graph_time_seconds,
            "vector_time_seconds": self.vector_time_seconds,
            "description_time_seconds": self.description_time_seconds,
            "bm25_time_seconds": self.bm25_time_seconds,
            "files_changed": self.files_changed,
            "files_unchanged": self.files_unchanged,
            "is_incremental": self.is_incremental,
        }
