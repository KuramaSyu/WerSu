"""Embedding generator interface and concrete model loaders.

Two backends are provided:

- :class:`SentenceTransformerEmbeddingGenerator` -- wraps
  HuggingFace ``sentence-transformers`` (PyTorch).  Kept as a
  fallback / opt-in; pulls a 500 MB+ torch wheel.
- :class:`FastEmbedEmbeddingGenerator` -- wraps Qdrant's
  ``fastembed`` (ONNX Runtime only).  This is the production
  backend: no torch, no ``libgomp``, ~50 MB wheel.

Both implement :class:`EmbeddingGeneratorABC`.  Callers receive
the abstract type and never need to know which backend is wired.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
import os
from typing import Any, Sequence

import numpy as np

from src.api import LoggingProvider


class Models(Enum):
    MINI_LM_L6_V2 = "sentence-transformers/all-MiniLM-L6-v2"
    PARAPHRASE_MPNET_BASE_V2 = "sentence-transformers/paraphrase-mpnet-base-v2"
    DISTILBERT_BASE_NLI_STSB_ELECTRA = "sentence-transformers/distilbert-base-nli-stsb-mean-tokens"


class EmbeddingGeneratorABC(ABC):
    """Abstract base class for embedding generators."""

    @abstractmethod
    def generate(self, text: str) -> Any:
        """Encode ``text`` into a 1-D vector.

        The return value must implement ``tolist()`` returning
        floats (torch tensor, numpy ndarray, etc.) so callers
        can format it as a pgvector literal.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the string name of the model."""
        ...

    @staticmethod
    def tensor_to_str_vec(tensor: Any) -> str:
        """Serialize a 1-D tensor / numeric iterable as ``[x,y,z]``."""
        return f"[{','.join(str(x) for x in tensor.tolist())}]"

    @staticmethod
    def tensor_to_sequence(tensor: Any) -> Sequence[float]:
        """Extract the floats from a 1-D tensor / numeric iterable."""
        return [float(x) for x in tensor.tolist()]

    @staticmethod
    def str_vec_to_list(vec_str: str) -> Sequence[float]:
        """Parse a ``[x,y,z]`` string back into a list of floats."""
        vec_str = vec_str.strip().lstrip("[").rstrip("]")
        if not vec_str:
            return []
        return [float(x) for x in vec_str.split(",")]

    @staticmethod
    def list_to_str_vec(vec_list: Sequence[float]) -> str:
        """Encode an in-memory sequence of floats as ``[x,y,z]``."""
        return f"[{','.join(str(x) for x in vec_list)}]"


def _default_cache_folder() -> str:
    """Resolve the model cache directory, creating it if missing."""
    cache_folder = os.path.expanduser("~/models")
    os.makedirs(cache_folder, exist_ok=True)
    return cache_folder


class SentenceTransformerEmbeddingGenerator(EmbeddingGeneratorABC):
    """Embedding generator backed by HuggingFace ``sentence-transformers``.

    Pulls a 500 MB+ torch wheel.  Kept as an opt-in fallback for
    users that prefer the PyTorch numerics over fastembed's
    ONNX output.  Production should use
    :class:`FastEmbedEmbeddingGenerator`.
    """

    def __init__(self, model_name: Models, logging_provider: LoggingProvider):
        from sentence_transformers import SentenceTransformer

        cache_folder = _default_cache_folder()
        model_path = model_name.value

        # 1. try to load from cache
        try:
            self.model = SentenceTransformer(
                model_path,
                cache_folder=cache_folder,
                local_files_only=True,
            )
        except Exception:
            # 2. fall back to download from huggingface
            try:
                self.model = SentenceTransformer(
                    model_path,
                    cache_folder=cache_folder,
                    local_files_only=False,
                )
            except RuntimeError as e:
                if "Cannot send a request, as the client has been closed." in str(e):
                    raise RuntimeError(
                        f"Failed to load embedding model '{model_path}'. "
                        f"It is not present in the cache '{cache_folder}' "
                        f"and no network connection is available to download it. "
                    ) from e
                raise

        self.model_enum = model_name
        self.log = logging_provider(__name__, self)

    def generate(self, text: str) -> Any:
        """Encode ``text`` using the loaded ``SentenceTransformer``."""
        start = datetime.now()
        embedding = self.model.encode(text)
        self.log.debug(f"Embedding generation took: {datetime.now() - start}")
        return embedding

    @property
    def model_name(self) -> str:
        return self.model_enum.value


class FastEmbedEmbeddingGenerator(EmbeddingGeneratorABC):
    """Embedding generator backed by Qdrant ``fastembed`` (ONNX Runtime).

    No torch dependency.  Uses ONNX Runtime directly, ~50 MB
    wheel.  Same model id namespace as sentence-transformers
    (``"sentence-transformers/all-MiniLM-L6-v2"``); fastembed
    resolves it from its own built-in model registry.
    """

    def __init__(self, model_name: Models, logging_provider: LoggingProvider):
        from fastembed import TextEmbedding

        cache_folder = _default_cache_folder()

        # ``TextEmbedding`` downloads on first use into ``cache_dir``
        # and reuses it on subsequent loads.  No explicit
        # local-only fallback like sentence-transformers; if the
        # cache is missing, fastembed re-downloads (network
        # required).
        self.model = TextEmbedding(
            model_name=model_name.value,
            cache_dir=cache_folder,
        )
        self.model_enum = model_name
        self.log = logging_provider(__name__, self)

    def generate(self, text: str) -> np.ndarray:
        """Encode ``text`` into a 1-D ``np.ndarray`` via ONNX Runtime."""
        start = datetime.now()
        # fastembed exposes a generator of np.ndarrays; pull the
        # one result for the single input.
        embedding = next(iter(self.model.embed([text])))
        self.log.debug(f"Embedding generation took: {datetime.now() - start}")
        return embedding

    @property
    def model_name(self) -> str:
        return self.model_enum.value


#: Backwards-compatible alias -- callers that imported the old
#: class name continue to work.  New code should pick one of the
#: concrete classes explicitly to make the backend obvious.
EmbeddingGenerator = FastEmbedEmbeddingGenerator


__all__ = [
    "EmbeddingGeneratorABC",
    "EmbeddingGenerator",
    "SentenceTransformerEmbeddingGenerator",
    "FastEmbedEmbeddingGenerator",
    "Models",
]