"""Text-to-media search over the FAISS index."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import config, db
from .embedder import get_embedder
from .index import get_index

MOMENTS_PER_RESULT = 4
# When nothing clears the confidence bar we still show a few best guesses
# instead of an empty screen.
GUESSES_WHEN_WEAK = 3
# Below this many stored vectors the null distribution is too noisy to trust.
MIN_CALIBRATION = 16

_calibration_lock = threading.Lock()
_calibration: dict[str, Any] = {"index": None, "version": None, "dim": None, "vectors": None}

EMPTY_RESPONSE: dict[str, Any] = {
    "query": "",
    "results": [],
    "candidates": 0,
    "strong_count": 0,
    "weak_count": 0,
    "weak_only": False,
    "cutoff": None,
    "cutoff_z": None,
    "show_all": False,
    "took_ms": 0.0,
    "timings": {},
}


def _sample_for(index) -> np.ndarray | None:
    """A cached random sample of library vectors, used to calibrate confidence.

    Recomputed only when the index changes (``index.version``). The sample is
    the null distribution: how similar an *arbitrary* item tends to be to the
    query. A real match should sit well above it.
    """
    version = getattr(index, "version", 0)
    with _calibration_lock:
        if (
            _calibration["index"] is index
            and _calibration["version"] == version
            and _calibration["dim"] == index.dim
            and _calibration["vectors"] is not None
        ):
            return _calibration["vectors"]
        vectors = db.sample_vectors(config.CALIBRATION_SAMPLES)
        if vectors.size == 0 or vectors.shape[1] != index.dim:
            vectors = None
        _calibration.update(index=index, version=version, dim=index.dim, vectors=vectors)
        return vectors


def _cutoff(sample: np.ndarray, vector: np.ndarray) -> tuple[float | None, float | None]:
    """Similarity above which a match counts as strong -> (cutoff, z used)."""
    if sample is None or sample.shape[0] < MIN_CALIBRATION:
        return None, None
    scores = sample @ vector
    mean = float(scores.mean())
    std = float(scores.std())
    if std <= 1e-6:
        # No spread means nothing stands out (for example a library of copies),
        # so fail closed instead of calling every result strong.
        return float(scores.max()) + 1.0, config.STRICT_Z
    return mean + config.STRICT_Z * std, config.STRICT_Z


def search(
    query: str,
    limit: int = 60,
    kind: str | None = None,
    index=None,
    embedder=None,
    show_all: bool = False,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {**EMPTY_RESPONSE, "query": query}

    embedder = embedder or get_embedder()
    index = index or get_index(dim=embedder.dim)

    t0 = time.perf_counter()
    embed_query = getattr(embedder, "embed_query", None)
    vector = embed_query(query) if embed_query else embedder.embed_text(query)
    t1 = time.perf_counter()

    # Over-fetch so grouping video moments into one result does not starve the
    # page, then trim to the requested number of distinct files. A type filter
    # needs a wider net because the filter is applied after the vector search.
    multiplier = 12 if kind is None else 40
    k = min(max(limit * multiplier, 200), 5000, max(index.count, 1))
    ids, scores = index.search(vector, k)
    t2 = time.perf_counter()

    sample = _sample_for(index)
    cutoff, cutoff_z = _cutoff(sample, vector)
    t3 = time.perf_counter()

    rows = db.items_with_media([int(i) for i in ids])
    t4 = time.perf_counter()

    by_media: dict[int, dict[str, Any]] = {}
    for item_id, score in zip(ids.tolist(), scores.tolist()):
        row = rows.get(int(item_id))
        if row is None:
            continue
        if kind in ("image", "video") and row["kind"] != kind:
            continue
        media_id = int(row["media_id"])
        entry = by_media.get(media_id)
        if entry is None:
            by_media[media_id] = {
                "media_id": media_id,
                "item_id": int(item_id),
                "path": row["path"],
                "name": Path(row["path"]).name,
                "dir": str(Path(row["path"]).parent),
                "kind": row["kind"],
                "t": row["t"],
                "score": round(float(score), 4),
                "thumb": f"/api/thumb/{int(item_id)}",
                "media_url": f"/api/media/{media_id}",
                "width": row["width"],
                "height": row["height"],
                "duration": row["duration"],
                "taken_at": row["taken_at"],
                "mtime": row["mtime"],
                "moments": [
                    {"item_id": int(item_id), "t": row["t"], "score": round(float(score), 4)}
                ],
            }
        elif len(entry["moments"]) < MOMENTS_PER_RESULT:
            entry["moments"].append(
                {"item_id": int(item_id), "t": row["t"], "score": round(float(score), 4)}
            )

    ranked = sorted(by_media.values(), key=lambda r: r["score"], reverse=True)
    if cutoff is None:
        strong = ranked
    else:
        strong = [entry for entry in ranked if entry["score"] >= cutoff]

    for entry in ranked:
        entry["strong"] = cutoff is None or entry["score"] >= cutoff
        entry["moments"].sort(key=lambda moment: (moment["t"] is None, moment["t"] or 0.0))

    if show_all:
        results = ranked[:limit]
    elif strong:
        results = strong[:limit]
    else:
        results = ranked[:GUESSES_WHEN_WEAK]

    t5 = time.perf_counter()
    return {
        "query": query,
        "results": results,
        "candidates": len(ids),
        "strong_count": len(strong),
        "weak_count": max(0, len(ranked) - len(strong)),
        "weak_only": bool(results) and not strong,
        "cutoff": round(cutoff, 4) if cutoff is not None else None,
        "cutoff_z": cutoff_z,
        "show_all": show_all,
        "took_ms": round((t5 - t0) * 1000, 2),
        "timings": {
            "embed_ms": round((t1 - t0) * 1000, 2),
            "faiss_ms": round((t2 - t1) * 1000, 2),
            "calibrate_ms": round((t3 - t2) * 1000, 2),
            "db_ms": round((t4 - t3) * 1000, 2),
            "group_ms": round((t5 - t4) * 1000, 2),
        },
    }


def benchmark(queries: list[str], limit: int = 60, runs: int = 5) -> dict[str, Any]:
    embedder = get_embedder()
    index = get_index(dim=embedder.dim)
    latencies: list[float] = []
    details = []
    for query in queries:
        for _ in range(runs):
            result = search(query, limit=limit, index=index, embedder=embedder)
            latencies.append(result["took_ms"])
            details.append(result["timings"])
    if not latencies:
        return {"runs": 0}
    arr = np.asarray(latencies)
    return {
        "runs": len(arr),
        "vectors": index.count,
        "mean_ms": round(float(arr.mean()), 2),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "max_ms": round(float(arr.max()), 2),
    }
