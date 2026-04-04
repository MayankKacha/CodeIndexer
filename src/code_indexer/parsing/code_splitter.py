"""
Code splitter – traverses a directory and splits files into CodeElements.

Orchestrates language detection, AST parsing, and file traversal to produce
a flat list of indexed code elements from an entire codebase.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from code_indexer.parsing.ast_parser import parse_file
from code_indexer.parsing.language_detector import (
    detect_language,
    should_skip_path,
)
from code_indexer.parsing.models import CodeElement, ParsedFile

logger = logging.getLogger(__name__)


def split_file(
    file_path: str | Path,
    repo_name: str = "",
    repo_root: str | Path = "",
) -> ParsedFile:
    """Parse a single file into code elements.

    Args:
        file_path: Absolute path to the source file.
        repo_name: Name of the repository.
        repo_root: Root directory of the repo (for relative paths).

    Returns:
        ParsedFile with extracted code elements.
    """
    file_path = Path(file_path)
    repo_root = Path(repo_root) if repo_root else file_path.parent

    language = detect_language(file_path)
    if not language:
        return ParsedFile(
            file_path=str(file_path),
            language="unknown",
            repo_name=repo_name,
            parse_errors=[f"Unsupported file type: {file_path.suffix}"],
        )

    try:
        relative_path = str(file_path.relative_to(repo_root))
    except ValueError:
        relative_path = str(file_path)

    try:
        elements = parse_file(
            file_path=file_path,
            language=language,
            repo_name=repo_name,
        )
        # Update file paths to be relative
        for el in elements:
            el.file_path = relative_path

        return ParsedFile(
            file_path=relative_path,
            language=language,
            repo_name=repo_name,
            elements=elements,
        )
    except Exception as e:
        logger.error(f"Error parsing {file_path}: {e}")
        return ParsedFile(
            file_path=relative_path,
            language=language,
            repo_name=repo_name,
            parse_errors=[str(e)],
        )


def split_directory(
    directory: str | Path,
    repo_name: str = "",
    ignore_patterns: List[str] | None = None,
) -> List[CodeElement]:
    """Walk a directory and split all supported files into code elements.

    Args:
        directory: Path to the directory to scan.
        repo_name: Name of the repository.
        ignore_patterns: Optional gitignore-style patterns to skip.

    Returns:
        Flat list of all CodeElements found.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    if not repo_name:
        repo_name = directory.name

    all_elements: List[CodeElement] = []
    files_parsed = 0
    files_skipped = 0

    # Load .gitignore / .codeindexerignore patterns
    spec = None
    if ignore_patterns:
        import pathspec
        spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)
    else:
        ignore_file = directory / ".codeindexerignore"
        if not ignore_file.exists():
            ignore_file = directory / ".gitignore"
        if ignore_file.exists():
            import pathspec
            patterns = ignore_file.read_text().splitlines()
            spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue

        # Skip hidden/build directories
        skip = False
        for parent in path.relative_to(directory).parents:
            if should_skip_path(directory / parent):
                skip = True
                break
        if skip:
            files_skipped += 1
            continue

        if should_skip_path(path):
            files_skipped += 1
            continue

        # Check against ignore patterns
        if spec:
            try:
                rel = str(path.relative_to(directory))
                if spec.match_file(rel):
                    files_skipped += 1
                    continue
            except ValueError:
                pass

        # Detect language
        language = detect_language(path)
        if not language:
            files_skipped += 1
            continue

        # Parse the file
        parsed = split_file(
            file_path=path,
            repo_name=repo_name,
            repo_root=directory,
        )

        if parsed.parse_errors:
            for err in parsed.parse_errors:
                logger.warning(f"Parse error in {parsed.file_path}: {err}")

        if parsed.elements:
            all_elements.extend(parsed.elements)
            files_parsed += 1
        else:
            files_skipped += 1

    logger.info(
        f"Split {files_parsed} files into {len(all_elements)} elements "
        f"({files_skipped} files skipped) in {repo_name}"
    )

    return all_elements


def split_codebase(
    path: str | Path,
    repo_name: str = "",
) -> tuple[List[CodeElement], dict]:
    """High-level API: split an entire codebase and return elements + stats.

    Args:
        path: Directory path to scan.
        repo_name: Optional repository name.

    Returns:
        Tuple of (elements, stats_dict).
    """
    elements = split_directory(path, repo_name=repo_name)

    # Build language stats
    lang_counts: dict[str, int] = {}
    type_counts = {"function": 0, "method": 0, "class": 0}
    total_lines = 0

    for el in elements:
        lang_counts[el.language] = lang_counts.get(el.language, 0) + 1
        if el.element_type in type_counts:
            type_counts[el.element_type] += 1
        total_lines += el.line_count

    stats = {
        "repo_name": repo_name or Path(path).name,
        "total_elements": len(elements),
        "functions": type_counts["function"],
        "methods": type_counts["method"],
        "classes": type_counts["class"],
        "total_lines": total_lines,
        "languages": lang_counts,
    }

    return elements, stats
