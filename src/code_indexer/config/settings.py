"""
Application settings using pydantic-settings.

All configuration is loaded from environment variables or a .env file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for CodeIndexer."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenAI ──────────────────────────────────────────────────────────
    openai_api_key: str = Field(
        default="sk-",
        description="OpenAI API key",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model for descriptions & compression",
    )
    openai_chat_model: str = Field(
        default="gpt-4o",
        description="Advanced OpenAI model for the RAG Assistant (reasoning)",
    )

    # ── Graph Backend ───────────────────────────────────────────────────
    graph_backend: str = Field(
        default="networkx",
        description="Graph database backend to use: 'networkx' or 'neo4j'",
    )

    # ── Neo4j (if used) ─────────────────────────────────────────────────
    neo4j_uri: str = Field(
        default="bolt://localhost:7687", description="Neo4j connection URI"
    )
    neo4j_username: str = Field(default="neo4j", description="Neo4j username")
    neo4j_password: str = Field(default="", description="Neo4j password")

    # ── Milvus ──────────────────────────────────────────────────────────
    milvus_uri: str = Field(
        default="./milvus_code.db",
        description="Milvus URI – file path for Lite, URL for cloud",
    )
    milvus_token: str = Field(default="", description="Milvus/Zilliz token")
    milvus_collection: str = Field(
        default="code_elements", description="Milvus collection name"
    )

    # ── Encoder ─────────────────────────────────────────────────────────
    encoder_model: str = Field(
        default="microsoft/codebert-base",
        description="SentenceTransformer model for code embeddings",
    )
    encoder_device: str = Field(
        default="cpu",
        description="Device for encoder: cpu | cuda | mps",
    )
    embedding_dim: int = Field(default=768, description="Embedding dimension")

    # ── Search ──────────────────────────────────────────────────────────
    bm25_weight: float = Field(
        default=0.6, description="Weight for BM25 in hybrid fusion"
    )
    vector_weight: float = Field(
        default=0.4, description="Weight for vector search in hybrid fusion"
    )
    rerank_top_k: int = Field(default=50, description="Candidates to pass to re-ranker")
    final_top_k: int = Field(default=10, description="Final results to return")

    # ── Compression ─────────────────────────────────────────────────────
    compression_strategy: str = Field(
        default="hybrid",
        description="Compression strategy: extractive | summary | hybrid",
    )
    max_compressed_tokens: int = Field(
        default=2000, description="Max tokens in compressed output"
    )

    # ── Cache ───────────────────────────────────────────────────────────
    cache_dir: str = Field(
        default="./.codeindexer_cache", description="Cache directory"
    )
    cache_ttl: int = Field(default=3600, description="Cache TTL in seconds")

    # ── API Server ──────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", description="API host")
    api_port: int = Field(default=8000, description="API port")

    # ── Cloning ─────────────────────────────────────────────────────────
    clone_dir: str = Field(
        default="./.cloned_repos", description="Directory for cloned repos"
    )

    def auto_detect_device(self) -> str:
        """Auto-detect the best available device."""
        if self.encoder_device != "cpu":
            return self.encoder_device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
