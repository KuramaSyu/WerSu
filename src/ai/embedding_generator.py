from abc import ABC, abstractclassmethod, abstractmethod, abstractstaticmethod
from datetime import datetime
from enum import Enum
import os

import numpy as np

from typing import TYPE_CHECKING, List, Any, Sequence

from torch import Tensor
from src.api import LoggingProvider


class Models(Enum):
    MINI_LM_L6_V2 = "sentence-transformers/all-MiniLM-L6-v2"
    PARAPHRASE_MPNET_BASE_V2 = "sentence-transformers/paraphrase-mpnet-base-v2"
    DISTILBERT_BASE_NLI_STSB_ELECTRA = "sentence-transformers/distilbert-base-nli-stsb-mean-tokens"

class EmbeddingGeneratorABC(ABC):
    """Abstract base class for embedding generators."""


    @abstractmethod
    def generate(self, text: str) -> Tensor:
        pass

    @staticmethod
    def tensor_to_str_vec(tensor: Tensor) -> str:
        """
        Convert a tensor to a compact string representation of a vector.

        Args
        ----
        tensor : Tensor
            A tensor-like object that implements tolist() (e.g., torch.Tensor,
            numpy.ndarray). Intended for 1-D tensors.
        
        Returns
        -------
        str
            A string representing the tensor as a bracketed, comma-separated vector.

        Examples
        ---------
        - 1-D tensor `[1.0, 2.0, 3.0]` -> `"[1.0,2.0,3.0]"`
        - 2-D tensor `[[1, 2], [3, 4]]` -> `"[[1,2],[3,4]]"`
        """
        return f"[{','.join(str(x) for x in tensor.tolist())}]"

    @staticmethod
    def tensor_to_sequence(tensor: Tensor) -> Sequence[float]:
        """
        Convert a tensor to a sequence of floats.

        Args
        ----
        tensor : Tensor
            A tensor-like object that implements tolist() (e.g., torch.Tensor,
            numpy.ndarray). Intended for 1-D tensors.
        
        Returns
        -------
        Sequence[float]
            A sequence of floats extracted from the tensor.

        Examples
        ---------
        - 1-D tensor `[1.0, 2.0, 3.0]` -> `[1.0, 2.0, 3.0]`
        - 2-D tensor `[[1, 2], [3, 4]]` -> `[1.0, 2.0, 3.0, 4.0]`
        """
        return [float(x) for x in tensor.tolist()]
       

    @staticmethod
    def str_vec_to_list(vec_str: str) -> Sequence[float]:
        """
        Convert a string representation of a vector back to a list of floats.

        Args
        ----
        vec_str : str
            A string representing a vector, formatted as a bracketed,
            comma-separated list (e.g., `"[1.0,2.0,3.0]"`).

        Returns
        -------
        Sequence[np.float32]
            A list of floats extracted from the string representation.

        Examples
        ---------
        - Input: `"[1.0,2.0,3.0]"` -> Output: `[1.0, 2.0, 3.0]`
        - Input: `"[[1,2],[3,4]]"` -> Output: `[[1.0, 2.0], [3.0, 4.0]]`
        """
        vec_str = vec_str.strip().lstrip("[").rstrip("]")
        if not vec_str:
            return []
        return [float(x) for x in vec_str.split(",")]

    @staticmethod
    def list_to_str_vec(vec_list: Sequence[float]) -> str:
        """
        Convert a list of floats to a string representation of a vector.

        Args
        ----
        vec_list : Sequence[float]
            A sequence of floats to be converted into a string representation.

        Returns
        -------
        str
            A string representing the list as a bracketed, comma-separated vector.

        Examples
        ---------
        - Input: `[1.0, 2.0, 3.0]` -> Output: `"[1.0,2.0,3.0]"`
        - Input: `[[1.0, 2.0], [3.0, 4.0]]` -> Output: `"[[1.0,2.0],[3.0,4.0]]"`
        """
        return f"[{','.join(str(x) for x in vec_list)}]"

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the string name of the model."""
        ...


class EmbeddingGenerator(EmbeddingGeneratorABC):
    """Generates embeddings for given text using specified model.
    the model is downloaded from the given model name. first it tries 
    to load from cache, then it falls back to download from hugging face
    """

    def __init__(self, model_name: Models, logging_provider: LoggingProvider):
        from sentence_transformers import SentenceTransformer

        cache_folder = os.path.expanduser("~/models")
        model_path = model_name.value

        # 1. try to load model from cache
        try:
            self.model = SentenceTransformer(
                model_path,
                cache_folder=cache_folder,
                local_files_only=True,
            )
        except Exception as cache_err:
            # 2. download model - network connection required
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

    def generate(self, text: str) -> Tensor:
        start = datetime.now()
        embedding = self.model.encode(text)
        self.log.debug(f"Embedding generation took: {datetime.now() - start}")
        return embedding

    @property
    def model_name(self) -> str:
        return self.model_enum.value