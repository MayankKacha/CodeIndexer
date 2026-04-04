"""
Git repository cloner for indexing remote GitHub repositories.

Supports cloning by URL, specific branches/tags, and caching of
previously cloned repositories.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def extract_repo_name(url: str) -> str:
    """Extract repository name from a GitHub URL.

    Examples:
        https://github.com/user/repo  →  "repo"
        https://github.com/user/repo.git  →  "repo"
        git@github.com:user/repo.git  →  "repo"
    """
    # Remove trailing .git
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    # Extract last path component
    parts = re.split(r"[/:]", url)
    return parts[-1] if parts else "unknown"


def clone_repository(
    url: str,
    clone_dir: str = "./.cloned_repos",
    branch: Optional[str] = None,
    depth: int = 1,
    force: bool = False,
) -> Path:
    """Clone a Git repository.

    Args:
        url: Repository URL (HTTPS or SSH).
        clone_dir: Base directory for cloned repos.
        branch: Specific branch or tag to clone.
        depth: Clone depth (1 for shallow clone).
        force: Force re-clone even if directory exists.

    Returns:
        Path to the cloned repository directory.
    """
    import git

    repo_name = extract_repo_name(url)
    target_dir = Path(clone_dir) / repo_name

    if target_dir.exists():
        if force:
            logger.info(f"Removing existing clone: {target_dir}")
            shutil.rmtree(target_dir)
        else:
            logger.info(f"Repository already cloned: {target_dir}")
            # Pull latest changes
            try:
                repo = git.Repo(target_dir)
                repo.remotes.origin.pull()
                logger.info(f"Pulled latest changes for {repo_name}")
            except Exception as e:
                logger.warning(f"Could not pull updates: {e}")
            return target_dir

    # Clone
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    clone_args = {
        "url": url,
        "to_path": str(target_dir),
        "depth": depth,
    }

    if branch:
        clone_args["branch"] = branch

    logger.info(f"Cloning {url} to {target_dir}...")

    try:
        git.Repo.clone_from(**clone_args)
        logger.info(f"Successfully cloned {repo_name}")
    except git.GitCommandError as e:
        logger.error(f"Clone failed: {e}")
        raise RuntimeError(f"Failed to clone {url}: {e}")

    return target_dir


def is_github_url(path: str) -> bool:
    """Check if a string is a GitHub URL."""
    return any(
        pattern in path.lower()
        for pattern in ["github.com", "gitlab.com", "bitbucket.org", "git@"]
    ) or path.endswith(".git")
