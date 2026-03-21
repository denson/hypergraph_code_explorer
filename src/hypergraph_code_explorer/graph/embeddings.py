"""
Node Embedding Manager
=======================
Uses all-MiniLM-L6-v2 (384-dim) by default. This model was trained for
semantic textual similarity and produces meaningfully different embeddings
for short identifiers — unlike CodeBERT which was never trained as a
sentence-transformers model and produces near-identical vectors for short
tokens due to [CLS]/[SEP] overhead dominating the mean pool.

Hugging Face Hub: models are fetched by ``sentence_transformers``, which uses
``huggingface_hub``. For authenticated downloads or higher rate limits, set
``HF_TOKEN`` (preferred) or ``HUGGING_FACE_HUB_TOKEN`` — see
https://huggingface.co/settings/tokens
"""

from __future__ import annotations

import os
import pickle
import re
from pathlib import Path

import numpy as np


def _load_dotenv_if_available() -> None:
    """So HF_TOKEN in .env is visible even when EmbeddingManager is used alone."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _hf_download_instructions() -> str:
    return (
        "Embedding models are downloaded from Hugging Face Hub (via sentence-transformers).\n"
        "If download fails or you hit rate limits / auth errors, set a read token:\n"
        "  • Environment variable: HF_TOKEN (preferred) or HUGGING_FACE_HUB_TOKEN\n"
        "  • Create a token: https://huggingface.co/settings/tokens\n"
        "  • Put HF_TOKEN=... in .env or export it in your shell.\n"
        "huggingface_hub picks up these variables automatically."
    )


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
            _load_dotenv_if_available()
            if self.verbose:
                if os.environ.get("HF_TOKEN") or os.environ.get(
                    "HUGGING_FACE_HUB_TOKEN"
                ):
                    print(
                        "  Hugging Face Hub: HF_TOKEN / HUGGING_FACE_HUB_TOKEN is set "
                        "(authenticated downloads)"
                    )
                else:
                    print(
                        "  Hugging Face Hub: no token in env — anonymous download "
                        "(set HF_TOKEN if you hit rate limits; see https://huggingface.co/settings/tokens)"
                    )
            from sentence_transformers import SentenceTransformer

            try:
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load embedding model {self.model_name!r}.\n\n"
                    f"{_hf_download_instructions()}\n\n"
                    f"Original error: {e!r}"
                ) from e
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

    # ---- keyword helpers ---------------------------------------------------

    @staticmethod
    def _extract_identifiers(query: str) -> list[str]:
        """Extract identifier-like tokens from a query string.

        Splits on whitespace, dots, and underscores, then lowercases and
        deduplicates while preserving order. Does NOT split camelCase so
        that e.g. "HTTPAdapter" stays as "httpadapter" — avoiding false
        substring matches against "HTTPDigestAuth", "HTTPError", etc.
        """
        raw_tokens = re.split(r'[\s.,;:!?(){}\[\]"\'`/\\]+', query)
        seen: set[str] = set()
        result: list[str] = []
        for tok in raw_tokens:
            for part in tok.split("_"):
                low = part.lower()
                if low and low not in seen and len(low) >= 3:
                    seen.add(low)
                    result.append(low)
        return result

    def _keyword_scores(
        self,
        query: str,
        nodes: list[str],
    ) -> np.ndarray:
        """Compute keyword match scores for each node against query identifiers.

        Filters out common English stopwords and short tokens, then returns
        a high fixed score for any node containing a significant identifier:
        1.0 for an exact match, 0.85 for a substring match. This ensures
        identifier hits dominate over weak embedding similarities.
        """
        query_ids = self._extract_identifiers(query)
        # Filter to significant identifiers only
        STOPWORDS = {
            "how", "does", "what", "why", "when", "where", "call", "calls",
            "the", "and", "for", "that", "this", "with", "from", "into",
            "use", "uses", "used", "get", "set", "has", "have", "can",
            "will", "would", "should", "which", "each", "some", "any",
        }
        sig_ids = [t for t in query_ids if t not in STOPWORDS and len(t) >= 3]
        if not sig_ids:
            return np.zeros(len(nodes), dtype=np.float32)

        scores = np.zeros(len(nodes), dtype=np.float32)
        for i, node in enumerate(nodes):
            node_lower = node.lower()
            best = 0.0
            for qid in sig_ids:
                if qid == node_lower:
                    best = 1.0
                    break  # exact match — can't do better
                elif qid in node_lower:
                    best = max(best, 0.85)  # substring match
            scores[i] = best
        return scores

    # ---- similarity --------------------------------------------------------

    def top_k_similar(
        self,
        query: str,
        k: int = 10,
        threshold: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Find the top-k nodes most similar to a query string.

        Uses hybrid matching: for each node the final score is
        ``max(embedding_similarity, keyword_score)`` so that exact identifier
        matches are never missed even when the embedding model under-scores
        them.
        """
        if not self._embeddings:
            return []

        q_vec = self.embed_query(query)
        nodes = list(self._embeddings.keys())
        matrix = np.stack([self._embeddings[n] for n in nodes])

        # Cosine similarity (embeddings are already normalised)
        embed_scores = matrix @ q_vec

        # Keyword match scores
        kw_scores = self._keyword_scores(query, nodes)

        # Hybrid: take the max per node
        scores = np.maximum(embed_scores, kw_scores)

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
