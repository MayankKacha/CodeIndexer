"""
Connector for the real CodeGraphContext (CGC) engine.
Interfaces with the codegraphcontext library to provide standardized context retrieval.
"""

import os
import logging
from typing import List, Dict, Any

try:
    from codegraphcontext.core import get_database_manager
    from codegraphcontext.tools.code_finder import CodeFinder
    CGC_AVAILABLE = True
except ImportError:
    CGC_AVAILABLE = False

logger = logging.getLogger(__name__)

class CGCConnector:
    """Wrapper for the actual codegraphcontext engine."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        if not CGC_AVAILABLE:
            raise ImportError("codegraphcontext is not installed. Run 'pip install codegraphcontext kuzu'")
        
        # Ensure we use Kuzu for this benchmark
        os.environ["DATABASE_TYPE"] = "kuzudb"
        self.db_manager = get_database_manager()
        self.finder = CodeFinder(self.db_manager)

    def search_context(self, query: str, top_k: int = 5) -> str:
        """
        Perform a search and return a formatted context string.
        Uses CGC's 'find_related_code' logic which aggregates multiple search types.
        """
        try:
            # CGC's 'find_related_code' returns a ranked list of matches
            results = self.finder.find_related_code(
                user_query=query,
                fuzzy_search=True,
                edit_distance=2,
                repo_path=self.repo_path
            )
            
            matches = results.get("ranked_results", [])[:top_k]
            
            if not matches:
                return ""

            # Format in the same style as CodeIndexer for a fair comparison
            context_parts = []
            for r in matches:
                name = r.get("name", "unknown")
                path = r.get("path", "unknown")
                line = r.get("line_number", "?")
                code = r.get("source", "")
                
                context_parts.append(
                    f"File: {path} | Component: {name}\n"
                    f"Lines {line}-?\n"
                    f"```python\n{code}\n```"
                )
            
            return "\n\n".join(context_parts)
            
        except Exception as e:
            logger.error(f"CGC search failed: {e}")
            return ""

    def get_stats(self) -> Dict[str, Any]:
        """Get database stats from CGC."""
        try:
            # We can use a simple Cypher query via the driver
            driver = self.db_manager.get_driver()
            with driver.session() as session:
                res = session.run("MATCH (n) RETURN count(n) as total_nodes")
                total_nodes = res.data()[0]["total_nodes"]
                return {"total_nodes": total_nodes}
        except Exception:
            return {}
