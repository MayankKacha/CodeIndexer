"""
Milvus vector database store for code embeddings.

Stores code element embeddings with rich metadata for semantic search.
Supports both Milvus Lite (local file) and remote Milvus/Zilliz Cloud.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from code_indexer.parsing.models import CodeElement

logger = logging.getLogger(__name__)


class MilvusStore:
    """Milvus vector database interface for code embeddings."""

    def __init__(
        self,
        uri: str = "./milvus_code.db",
        token: str = "",
        collection_name: str = "code_elements",
        embedding_dim: int = 768,
    ):
        from pymilvus import MilvusClient

        self.collection_name = collection_name
        self.embedding_dim = embedding_dim

        connect_params = {"uri": uri}
        if token:
            connect_params["token"] = token

        self.client = MilvusClient(**connect_params)
        self._ensure_collection()
        logger.info(f"Connected to Milvus at {uri}")

    def _ensure_collection(self):
        """Create the collection if it doesn't exist."""
        from pymilvus import CollectionSchema, DataType, FieldSchema

        if self.client.has_collection(self.collection_name):
            logger.info(f"Collection '{self.collection_name}' already exists")
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)

        # Primary key
        schema.add_field(
            field_name="id",
            datatype=DataType.VARCHAR,
            max_length=512,
            is_primary=True,
        )

        # Vector field
        schema.add_field(
            field_name="embedding",
            datatype=DataType.FLOAT_VECTOR,
            dim=self.embedding_dim,
        )

        # Metadata fields
        schema.add_field(field_name="element_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="name", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="qualified_name", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="file_path", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="repo_name", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="language", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="start_line", datatype=DataType.INT64)
        schema.add_field(field_name="end_line", datatype=DataType.INT64)
        schema.add_field(field_name="code", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="signature", datatype=DataType.VARCHAR, max_length=2048)
        schema.add_field(field_name="description", datatype=DataType.VARCHAR, max_length=4096)
        schema.add_field(field_name="docstring", datatype=DataType.VARCHAR, max_length=4096)
        schema.add_field(field_name="parent_class", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="complexity", datatype=DataType.INT64)

        # Create the collection
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
        )

        # Create index for fast ANN search
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        self.client.create_index(
            collection_name=self.collection_name,
            index_params=index_params,
        )

        logger.info(f"Created collection '{self.collection_name}' with AUTOINDEX")

    def insert_elements(
        self,
        elements: List[CodeElement],
        embeddings: List[List[float]],
        batch_size: int = 100,
    ) -> int:
        """Insert code elements with their embeddings.

        Args:
            elements: Code elements to insert.
            embeddings: Corresponding embedding vectors.
            batch_size: Number of elements to insert per batch.

        Returns:
            Number of elements inserted.
        """
        if len(elements) != len(embeddings):
            raise ValueError(
                f"Mismatch: {len(elements)} elements vs {len(embeddings)} embeddings"
            )

        total_inserted = 0

        for i in range(0, len(elements), batch_size):
            batch_elements = elements[i : i + batch_size]
            batch_embeddings = embeddings[i : i + batch_size]

            data = []
            for el, emb in zip(batch_elements, batch_embeddings):
                record = {
                    "id": el.element_id,
                    "embedding": emb,
                    "element_type": el.element_type,
                    "name": el.name,
                    "qualified_name": el.qualified_name or el.name,
                    "file_path": el.file_path,
                    "repo_name": el.repo_name,
                    "language": el.language,
                    "start_line": el.start_line,
                    "end_line": el.end_line,
                    "code": el.code[:65000],  # Milvus VARCHAR limit
                    "signature": el.signature[:2000],
                    "description": el.description[:4000],
                    "docstring": (el.docstring or "")[:4000],
                    "parent_class": el.parent_class or "",
                    "complexity": el.complexity,
                }
                data.append(record)

            try:
                result = self.client.insert(
                    collection_name=self.collection_name,
                    data=data,
                )
                total_inserted += len(data)
            except Exception as e:
                logger.error(f"Failed to insert batch at offset {i}: {e}")

        logger.info(f"Inserted {total_inserted} elements into Milvus")
        return total_inserted

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_expr: str = "",
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar code elements by embedding.

        Args:
            query_embedding: The query vector.
            top_k: Number of results to return.
            filter_expr: Optional Milvus filter expression.
            output_fields: Fields to include in results.

        Returns:
            List of search results with metadata and scores.
        """
        if output_fields is None:
            output_fields = [
                "element_type", "name", "qualified_name", "file_path",
                "repo_name", "language", "start_line", "end_line",
                "code", "signature", "description", "docstring",
                "parent_class", "complexity",
            ]

        search_params = {
            "metric_type": "COSINE",
            "params": {"ef": 128},
        }

        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[query_embedding],
                limit=top_k,
                output_fields=output_fields,
                search_params=search_params,
                filter=filter_expr if filter_expr else None,
            )

            if results and len(results) > 0:
                hits = []
                for hit in results[0]:
                    record = {
                        "id": hit.get("id"),
                        "score": hit.get("distance", 0.0),
                    }
                    entity = hit.get("entity", {})
                    record.update(entity)
                    hits.append(record)
                return hits
            return []
        except Exception as e:
            logger.error(f"Milvus search failed: {e}")
            return []

    def search_by_repo(
        self,
        query_embedding: List[float],
        repo_name: str,
        top_k: int = 10,
    ) -> List[Dict]:
        """Search within a specific repository."""
        filter_expr = f'repo_name == "{repo_name}"'
        return self.search(query_embedding, top_k=top_k, filter_expr=filter_expr)

    def delete_by_repo(self, repo_name: str):
        """Delete all elements for a repository."""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'repo_name == "{repo_name}"',
            )
            logger.info(f"Deleted Milvus data for repository: {repo_name}")
        except Exception as e:
            logger.error(f"Failed to delete Milvus data: {e}")

    def delete_by_file(self, repo_name: str, file_path: str):
        """Delete all elements for a single file in a repository."""
        try:
            escaped = file_path.replace('"', '\\"')
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'repo_name == "{repo_name}" and file_path == "{escaped}"',
            )
            logger.info(f"Deleted Milvus data for {repo_name}:{file_path}")
        except Exception as e:
            logger.error(f"Failed to delete Milvus file data: {e}")

    def get_element_count(self, repo_name: str = "") -> int:
        """Get the number of elements in the collection."""
        try:
            stats = self.client.get_collection_stats(self.collection_name)
            return stats.get("row_count", 0)
        except Exception:
            return 0

    def drop_collection(self):
        """Drop the entire collection (destructive!)."""
        try:
            self.client.drop_collection(self.collection_name)
            logger.info(f"Dropped collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Failed to drop collection: {e}")
