"""
Node Embedding Manager
=======================
Uses all-MiniLM-L6-v2 (384-dim) by default. This model was trained for
semantic textual similarity and produces meaningfully different embeddings
for short identifiers — unlike CodeBERT which was never trained as a
sentence-transformers model and produces near-identical vectors for short
tokens due to [CLS]/[SEP] overhead dominating the mean pool.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np


class EmbeddingManager:
    """Manages node embeddings for the hypergraph."""

    DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        batch_size: int = 64,
        verbose: bool = False,
    ):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.verbose = verbose
        self._model = None
        self._embeddings: dict[str, np.ndarray] = {}

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
            if self.verbose:
                print(f"  Loaded embedding model: {self.model_name}")
        return self._model

    # ---- embedding ---------------------------------------------------------

    def embed_nodes(self, nodes: list[str]) -> dict[str, np.ndarray]:
        """Embed node names. Only processes nodes not already embedded."""
        new_nodes = [n for n in nodes if n not in self._embeddings]
        if not new_nodes:
            return {}

        model = self._get_model()
        if self.verbose:
            print(f"  Embedding {len(new_nodes)} new nodes...")

        new_embeddings: dict[str, np.ndarray] = {}
        for i in range(0, len(new_nodes), self.batch_size):
            batch = new_nodes[i : i + self.batch_size]
            vecs = model.encode(
                batch,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            for node, vec in zip(batch, vecs):
                new_embeddings[node] = vec
                self._embeddings[node] = vec

        return new_embeddings

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a query string."""
        model = self._get_model()
        vec = model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
        return vec

    def embed_all_from_builder(self, builder) -> None:
        """Embed all nodes currently in a HypergraphBuilder."""
        all_nodes = builder.get_all_nodes()
        self.embed_nodes(list(all_nodes))

    # ---- similarity --------------------------------------------------------

    def top_k_similar(
        self,
        query: str,
        k: int = 10,
        threshold: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Find the top-k nodes most similar to a query string."""
        if not self._embeddings:
            return []

        q_vec = self.embed_query(query)
        nodes = list(self._embeddings.keys())
        matrix = np.stack([self._embeddings[n] for n in nodes])

        # Cosine similarity (embeddings are already normalised)
        scores = matrix @ q_vec

        order = np.argsort(-scores)
        results = []
        for idx in order[:k]:
            score = float(scores[idx])
            if score >= threshold:
                results.append((nodes[idx], score))
        return results

    def similarity(self, node_a: str, node_b: str) -> float:
        """Cosine similarity between two node embeddings."""
        if node_a not in self._embeddings or node_b not in self._embeddings:
            return 0.0
        return float(np.dot(self._embeddings[node_a], self._embeddings[node_b]))

    def get(self, node: str) -> np.ndarray | None:
        return self._embeddings.get(node)

    def remove(self, node: str) -> None:
        self._embeddings.pop(node, None)

    def rename(self, old: str, new: str) -> None:
        """Rename a node in the embedding store."""
        if old in self._embeddings:
            self._embeddings[new] = self._embeddings.pop(old)

    def __len__(self) -> int:
        return len(self._embeddings)

    def __contains__(self, node: str) -> bool:
        return node in self._embeddings

    @property
    def embeddings(self) -> dict[str, np.ndarray]:
        return self._embeddings

    # ---- serialisation -----------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {"model_name": self.model_name, "embeddings": self._embeddings}, f
            )

    @classmethod
    def load(cls, path: str | Path, device: str = "cpu") -> EmbeddingManager:
        with open(path, "rb") as f:
            data = pickle.load(f)
        manager = cls(model_name=data["model_name"], device=device)
        manager._embeddings = data["embeddings"]
        return manager
