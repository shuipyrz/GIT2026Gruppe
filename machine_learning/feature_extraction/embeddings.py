"""Name embeddings from the configured multilingual SentenceTransformer."""

import re

import numpy as np

from .config import EMBEDDING_DIMENSION, EMBEDDING_MODEL


def normalize_field_name(name: str) -> str:
    return re.sub(r"[_\s]+", " ", str(name)).strip()


def embed_names(names, model_name: str = EMBEDDING_MODEL, model=None) -> np.ndarray:
    if model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("Install sentence-transformers to create pretrained field-name embeddings.") from exc
        model = SentenceTransformer(model_name)
    vectors = np.asarray(model.encode([normalize_field_name(n) for n in names], convert_to_numpy=True), dtype=np.float32)
    if vectors.shape != (len(names), EMBEDDING_DIMENSION):
        raise ValueError(f"Expected embeddings with shape ({len(names)}, {EMBEDDING_DIMENSION}), got {vectors.shape}.")
    return vectors
