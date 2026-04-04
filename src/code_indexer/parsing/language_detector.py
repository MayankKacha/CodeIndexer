"""
Language detection based on file extensions.

Maps file extensions to tree-sitter language names and provides
utilities for filtering supported files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# ── Extension → Language mapping ────────────────────────────────────────
EXTENSION_MAP: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    # JavaScript / TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    # Java
    ".java": "java",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # C / C++
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    # C#
    ".cs": "c_sharp",
    # Ruby
    ".rb": "ruby",
    # PHP
    ".php": "php",
    # Kotlin
    ".kt": "kotlin",
    ".kts": "kotlin",
    # Swift
    ".swift": "swift",
    # Scala
    ".scala": "scala",
    # Lua
    ".lua": "lua",
    # Shell
    ".sh": "bash",
    ".bash": "bash",
}

# Files/dirs to always skip
SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".tox",
    ".eggs", ".mypy_cache", ".pytest_cache", "venv", ".venv", "env",
    ".env", "dist", "build", ".build", "target", ".idea", ".vscode",
    ".cloned_repos", ".codeindexer_cache", "vendor", "bower_components",
    ".next", ".nuxt",
}

SKIP_FILES = {
    ".DS_Store", "Thumbs.db", ".gitignore", ".gitattributes",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Pipfile.lock", "poetry.lock",
}

MAX_FILE_SIZE = 1_000_000  # 1 MB – skip huge generated files


def detect_language(file_path: str | Path) -> Optional[str]:
    """Detect the programming language from a file extension.

    Returns the tree-sitter language name or None if unsupported.
    """
    ext = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(ext)


def is_supported_file(file_path: str | Path) -> bool:
    """Check if a file is a supported source code file."""
    return detect_language(file_path) is not None


def should_skip_path(path: Path) -> bool:
    """Check if a path should be skipped during traversal."""
    name = path.name
    if path.is_dir():
        return name in SKIP_DIRS or name.startswith(".")
    if path.is_file():
        if name in SKIP_FILES:
            return True
        if path.stat().st_size > MAX_FILE_SIZE:
            return True
        if path.stat().st_size == 0:
            return True
    return False


def get_supported_extensions() -> list[str]:
    """Return all supported file extensions."""
    return sorted(EXTENSION_MAP.keys())


def get_supported_languages() -> list[str]:
    """Return all supported language names."""
    return sorted(set(EXTENSION_MAP.values()))
