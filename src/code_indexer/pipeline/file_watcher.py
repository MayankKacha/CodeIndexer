"""
File watcher for incremental index updates.

Watches a directory for file changes and triggers re-indexing of
modified files to keep the graph, vectors, and BM25 index up to date.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from code_indexer.parsing.language_detector import (
    detect_language,
    should_skip_path,
)

logger = logging.getLogger(__name__)


class CodeChangeHandler(FileSystemEventHandler):
    """Handle file system events for code files."""

    def __init__(
        self,
        on_change: Callable[[str, str], None],
        debounce_seconds: float = 2.0,
    ):
        """Initialize the handler.

        Args:
            on_change: Callback(file_path, event_type) for code changes.
            debounce_seconds: Minimum interval between processing same file.
        """
        super().__init__()
        self.on_change = on_change
        self.debounce_seconds = debounce_seconds
        self._last_processed: dict[str, float] = {}

    def _should_process(self, path: str) -> bool:
        """Check if this file should trigger re-indexing."""
        p = Path(path)

        # Skip non-code files
        if not detect_language(p):
            return False

        # Skip ignored paths
        if should_skip_path(p):
            return False

        # Debounce
        now = time.time()
        last = self._last_processed.get(path, 0)
        if now - last < self.debounce_seconds:
            return False

        self._last_processed[path] = now
        return True

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and self._should_process(event.src_path):
            logger.debug(f"File modified: {event.src_path}")
            self.on_change(event.src_path, "modified")

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory and self._should_process(event.src_path):
            logger.debug(f"File created: {event.src_path}")
            self.on_change(event.src_path, "created")

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            p = Path(event.src_path)
            if detect_language(p):
                logger.debug(f"File deleted: {event.src_path}")
                self.on_change(event.src_path, "deleted")


class FileWatcher:
    """Watch a directory for code changes and trigger re-indexing."""

    def __init__(
        self,
        directory: str | Path,
        on_change: Callable[[str, str], None],
        debounce_seconds: float = 2.0,
    ):
        self.directory = Path(directory).resolve()
        self.on_change = on_change
        self.observer = Observer()
        self.handler = CodeChangeHandler(on_change, debounce_seconds)

    def start(self):
        """Start watching for changes (non-blocking)."""
        self.observer.schedule(
            self.handler,
            str(self.directory),
            recursive=True,
        )
        self.observer.start()
        logger.info(f"Watching {self.directory} for changes...")

    def stop(self):
        """Stop watching."""
        self.observer.stop()
        self.observer.join()
        logger.info("File watcher stopped")

    def run_forever(self):
        """Start watching and block until interrupted."""
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
