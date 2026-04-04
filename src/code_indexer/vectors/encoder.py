"""
Code embedding encoder using CodeBERT (or compatible HuggingFace models).

Generates 768-dimensional vector embeddings for code elements using
microsoft/codebert-base. Supports batch encoding and GPU/MPS acceleration.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class CodeEncoder:
    """Encode code elements into vector embeddings using SentenceTransformers."""

    def __init__(
        self,
        model_name: str = "flax-sentence-embeddings/st-codesearch-distilroberta-base",
        device: str = "cpu",
        max_length: int = 512,
    ):
        """Initialize the encoder.

        Args:
            model_name: HuggingFace sentence-transformers model name.
            device: Device to run on (cpu, cuda, mps).
            max_length: Max token length for input.
        """
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self._model = None

    def _load_model(self):
        """Lazy-load the SentenceTransformer model."""
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading encoder model: {self.model_name} on {self.device}")

        # The sentence-transformer natively handles pooling and token limits correctly
        self._model = SentenceTransformer(self.model_name, device=self.device)
        self._model.max_seq_length = self.max_length

        logger.info(f"Encoder model loaded successfully on {self.device}")

    def encode(self, text: str) -> List[float]:
        """Encode a single text into a vector embedding.

        Args:
            text: The text to encode.

        Returns:
            List of floats representing the embedding vector.
        """
        self._load_model()
        
        # SentenceTransformer output is natively a cosine-ready numpy array
        embedding = self._model.encode(text, convert_to_numpy=True, show_progress_bar=False)
        return embedding.tolist()

    def encode_batch(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress: bool = True,
    ) -> List[List[float]]:
        """Encode multiple texts into vector embeddings.

        Args:
            texts: List of texts to encode.
            batch_size: Number of texts to process at once.
            show_progress: Whether to show a progress bar.

        Returns:
            List of embedding vectors.
        """
        self._load_model()
        
        logger.info(f"Encoding {len(texts)} texts with {self.model_name}...")
        embeddings = self._model.encode(
            texts, 
            batch_size=batch_size, 
            show_progress_bar=show_progress,
            convert_to_numpy=True
        )
        
        return [emb.tolist() for emb in embeddings]

    @property
    def embedding_dim(self) -> int:
        """Return the embedding dimension expected for this model."""
        return 768  # Both distilroberta and mpnet-base use 768

    def health_check(self) -> bool:
        """Verify the encoder is working."""
        try:
            result = self.encode("def hello(): pass")
            return len(result) == self.embedding_dim
        except Exception as e:
            logger.error(f"Encoder health check failed: {e}")
            return False
