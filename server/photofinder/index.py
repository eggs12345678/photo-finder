"""FAISS index wrapper.

A flat inner-product index over normalised vectors = exact cosine search.
At a million vectors this is still a couple of milliseconds, and it is
deterministic, which matters more here than ANN recall. The wrapper persists
to disk atomically and supports id-based removal.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable

import numpy as np

from . import config


class VectorIndex:
    def __init__(self, path: Path = config.INDEX_PATH, dim: int = 512) -> None:
        self.path = Path(path)
        self.dim = dim
        self._index = None
        self._lock = threading.RLock()
        self._load_or_create()

    def _new(self):
        import faiss

        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))

    def _load_or_create(self) -> None:
        import faiss

        if self.path.exists():
            index = faiss.read_index(str(self.path))
            if index.d != self.dim:
                raise ValueError(
                    f"Index dimension {index.d} does not match model dimension {self.dim}"
                )
            self._index = index
        else:
            self._index = self._new()

    def reset(self, dim: int | None = None) -> None:
        with self._lock:
            if dim:
                self.dim = dim
            self._index = self._new()
            self.save()

    def rebuild(self, rows: Iterable[tuple[int, bytes]], dim: int | None = None) -> int:
        """Replace the FAISS index with vectors persisted in the metadata DB."""
        with self._lock:
            if dim:
                self.dim = dim
            self._index = self._new()
            ids: list[int] = []
            vectors: list[np.ndarray] = []
            total = 0
            for item_id, blob in rows:
                vector = np.frombuffer(blob, dtype=np.float32)
                if vector.size != self.dim:
                    continue
                ids.append(item_id)
                vectors.append(vector)
                if len(ids) >= 4096:
                    self._index.add_with_ids(
                        np.ascontiguousarray(np.stack(vectors), dtype=np.float32),
                        np.asarray(ids, dtype=np.int64),
                    )
                    total += len(ids)
                    ids, vectors = [], []
            if ids:
                self._index.add_with_ids(
                    np.ascontiguousarray(np.stack(vectors), dtype=np.float32),
                    np.asarray(ids, dtype=np.int64),
                )
                total += len(ids)
            self.save()
            return total

    @property
    def count(self) -> int:
        with self._lock:
            return int(self._index.ntotal)

    def add(self, ids: list[int], vectors: np.ndarray) -> None:
        if not ids:
            return
        if vectors.shape != (len(ids), self.dim):
            raise ValueError(f"Expected {(len(ids), self.dim)} vectors, got {vectors.shape}")
        with self._lock:
            self._index.add_with_ids(
                np.ascontiguousarray(vectors, dtype=np.float32),
                np.asarray(ids, dtype=np.int64),
            )

    def remove(self, ids: list[int]) -> None:
        if not ids:
            return
        import faiss

        with self._lock:
            selector = faiss.IDSelectorBatch(np.asarray(ids, dtype=np.int64))
            self._index.remove_ids(selector)

    def search(self, vector: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if self._index.ntotal == 0:
                return np.zeros((0,), dtype=np.int64), np.zeros((0,), dtype=np.float32)
            k = min(k, int(self._index.ntotal))
            scores, ids = self._index.search(
                np.ascontiguousarray(vector.reshape(1, -1), dtype=np.float32), k
            )
        return ids[0], scores[0]

    def save(self) -> None:
        import faiss

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            faiss.write_index(self._index, str(tmp))
            os.replace(tmp, self.path)


_index: VectorIndex | None = None
_index_lock = threading.Lock()


def get_index(dim: int = 512) -> VectorIndex:
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = VectorIndex(dim=dim)
    return _index
