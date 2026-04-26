"""
Diff-aware impact analysis.

Given a unified diff (or a pair of git refs), find the code elements whose
line ranges overlap the changed lines, then run impact analysis on each
to surface direct + transitive callers and the files that would need to
be revisited in a code review.

Pure-Python parser: we don't depend on `unidiff` or `whatthepatch`. The
unified-diff hunk format is small and stable enough to handle inline.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Matches `@@ -OLD_START,OLD_COUNT +NEW_START,NEW_COUNT @@` and the count-1
# variants the format permits when count is 1.
_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_PLUSPLUS_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_MINUSMINUS_RE = re.compile(r"^--- (?:a/)?(.+)$")


@dataclass
class FileChange:
    file_path: str
    new_ranges: List[Tuple[int, int]] = field(default_factory=list)  # (start_line, end_line) inclusive
    is_deleted: bool = False
    is_added: bool = False


def parse_unified_diff(diff_text: str) -> List[FileChange]:
    """Parse a unified diff into per-file changed line ranges.

    Only post-change (new) ranges are returned — they map directly to lines
    in the currently-indexed file. Pure-deletion hunks are intentionally
    represented as zero-width ranges so the caller can still flag the file
    as touched without indexing a non-existent line.
    """
    files: List[FileChange] = []
    current: Optional[FileChange] = None
    seen_plus_path = False

    for raw in diff_text.splitlines():
        m = _DIFF_HEADER_RE.match(raw)
        if m:
            if current is not None:
                files.append(current)
            current = FileChange(file_path=m.group(2))
            seen_plus_path = False
            continue

        if current is None:
            continue

        if raw == "--- /dev/null":
            current.is_added = True
            continue
        if raw == "+++ /dev/null":
            current.is_deleted = True
            continue

        # Some diffs (e.g., `git diff` without index) skip the `diff --git`
        # header. Use the +++ line as a fallback file path.
        if not seen_plus_path:
            mp = _PLUSPLUS_RE.match(raw)
            if mp:
                if not current.file_path or current.file_path == "":
                    current.file_path = mp.group(1)
                seen_plus_path = True
                continue

        mh = _HUNK_RE.match(raw)
        if mh:
            new_start = int(mh.group(3))
            new_count = int(mh.group(4) or "1")
            if new_count == 0:
                # Pure deletion — record a single-line marker at new_start.
                current.new_ranges.append((new_start, new_start))
            else:
                current.new_ranges.append((new_start, new_start + new_count - 1))

    if current is not None:
        files.append(current)

    # Drop diffs without any file path (defensive).
    return [f for f in files if f.file_path]


def git_diff(repo_path: str, base_ref: str, head_ref: str = "HEAD") -> str:
    """Run `git diff --unified=0 base_ref head_ref` inside repo_path.

    --unified=0 keeps hunks tight which makes element-overlap detection
    far more precise than the default 3 lines of context.
    """
    cmd = ["git", "diff", "--unified=0", "--no-color", base_ref, head_ref]
    proc = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git diff failed ({proc.returncode}): {proc.stderr.strip() or 'no output'}"
        )
    return proc.stdout


def find_overlapping_elements(
    graph,
    repo_name: str,
    file_changes: List[FileChange],
) -> List[Dict[str, Any]]:
    """Return graph nodes whose (file_path, line range) overlaps any change.

    Matches by file_path equality on the relative path stored in the index.
    Callers should pass repo-relative paths in `file_changes` for best results.
    """
    changes_by_file: Dict[str, List[Tuple[int, int]]] = {}
    for fc in file_changes:
        changes_by_file.setdefault(fc.file_path, []).extend(fc.new_ranges or [(1, 1)])

    out: List[Dict[str, Any]] = []
    for _node_id, data in graph.nodes(data=True):
        if data.get("label") == "Repository":
            continue
        if repo_name and data.get("repo_name") != repo_name:
            continue
        fp = data.get("file_path") or ""
        if fp not in changes_by_file:
            continue
        el_start = int(data.get("start_line") or 0)
        el_end = int(data.get("end_line") or el_start)
        if el_start <= 0:
            continue
        ranges = changes_by_file[fp]
        if any(el_start <= rng_end and el_end >= rng_start for rng_start, rng_end in ranges):
            out.append({
                "name": data.get("name"),
                "qualified_name": data.get("qualified_name"),
                "element_type": data.get("element_type"),
                "file_path": fp,
                "start_line": el_start,
                "end_line": el_end,
                "language": data.get("language"),
            })
    out.sort(key=lambda r: (r.get("file_path", ""), r.get("start_line", 0)))
    return out


def diff_impact(
    pipeline,
    repo_name: str,
    diff_text: Optional[str] = None,
    repo_path: Optional[str] = None,
    base_ref: Optional[str] = None,
    head_ref: str = "HEAD",
    max_depth: int = 3,
) -> Dict[str, Any]:
    """End-to-end: turn a diff into changed elements + their impact closures."""
    if not diff_text:
        if not (repo_path and base_ref):
            raise ValueError(
                "Provide either `diff_text` or both `repo_path` and `base_ref`."
            )
        diff_text = git_diff(repo_path, base_ref, head_ref)

    file_changes = parse_unified_diff(diff_text)
    if not file_changes:
        return {
            "repo_name": repo_name,
            "files_touched": [],
            "elements_changed": [],
            "impact": [],
        }

    # Normalize to repo-relative paths if an absolute repo_path is known.
    if repo_path:
        root = Path(repo_path).resolve()
        for fc in file_changes:
            p = Path(fc.file_path)
            if p.is_absolute():
                try:
                    fc.file_path = str(p.resolve().relative_to(root))
                except ValueError:
                    pass

    graph = pipeline.graph_store.graph
    changed_elements = find_overlapping_elements(graph, repo_name, file_changes)

    # Run impact analysis once per changed element. Dedupe results across
    # the closure so callers downstream of multiple changes only appear once.
    gq = pipeline.graph_queries
    impact_by_target: Dict[str, Dict[str, Any]] = {}
    affected_files: set = set()
    for el in changed_elements:
        name = el.get("qualified_name") or el.get("name")
        if not name:
            continue
        try:
            res = gq.impact_analysis(name, max_depth=max_depth)
        except Exception as e:
            logger.warning(f"impact_analysis failed for {name}: {e}")
            continue
        impact_by_target[name] = {
            "target": name,
            "file_path": el.get("file_path"),
            "direct_callers": res.get("direct_callers", 0),
            "total_affected": res.get("total_affected", 0),
            "affected_files": res.get("affected_files", []),
        }
        for f in res.get("affected_files", []):
            if f:
                affected_files.add(f)

    return {
        "repo_name": repo_name,
        "files_touched": [fc.file_path for fc in file_changes],
        "elements_changed": changed_elements,
        "impact": list(impact_by_target.values()),
        "all_affected_files": sorted(affected_files),
    }
