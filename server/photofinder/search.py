"""Text-to-media search over the FAISS index, with optional OCR text matches.

CLIP handles what a file looks like. OCR text handles what a screenshot says:
the two signals are merged as ``max(clip, OCR_BOOST * text_coverage)`` so a
literal match can surface even when the picture looks like nothing in
particular, while conceptual queries keep their pure CLIP ranking.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import config, db, ocr
from .embedder import get_embedder
from .index import get_index

MOMENTS_PER_RESULT = 4
# When nothing clears the confidence bar we still show a few best guesses
# instead of an empty screen.
GUESSES_WHEN_WEAK = 3
# Below this many stored vectors the null distribution is too noisy to trust.
MIN_CALIBRATION = 16
OCR_OFF = {"off", "0", "false", "no", "none"}

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
    "text_matches": 0,
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


def _text_coverage(query: str) -> dict[int, float]:
    """Per-media fraction of the query's (IDF-weighted) words found in OCR text."""
    if config.OCR_MODE in OCR_OFF:
        return {}
    tokens = ocr.tokens(query)
    if not tokens:
        return {}
    postings, idf, _total = db.ocr_postings(tokens)
    denom = sum(idf.values())
    if not postings or denom <= 0:
        return {}
    weights: dict[int, float] = {}
    for token, media_ids in postings.items():
        weight = idf[token]
        for media_id in media_ids:
            weights[media_id] = weights.get(media_id, 0.0) + weight
    return {media_id: min(1.0, weight / denom) for media_id, weight in weights.items()}


def _entry(item_id: int, media_id: int, row, score: float) -> dict[str, Any]:
    return {
        "media_id": media_id,
        "item_id": item_id,
        "path": row["path"],
        "name": Path(row["path"]).name,
        "dir": str(Path(row["path"]).parent),
        "kind": row["kind"],
        "t": row["t"],
        "score": round(float(score), 4),
        "thumb": f"/api/thumb/{item_id}",
        "media_url": f"/api/media/{media_id}",
        "width": row["width"],
        "height": row["height"],
        "duration": row["duration"],
        "taken_at": row["taken_at"],
        "mtime": row["mtime"],
        "text_score": 0.0,
        "moments": [{"item_id": item_id, "t": row["t"], "score": round(float(score), 4)}],
    }


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

    coverage = _text_coverage(query)
    t5 = time.perf_counter()

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
            by_media[media_id] = _entry(int(item_id), media_id, row, float(score))
        elif len(entry["moments"]) < MOMENTS_PER_RESULT:
            entry["moments"].append(
                {"item_id": int(item_id), "t": row["t"], "score": round(float(score), 4)}
            )

    # Text-only matches may not be in the CLIP candidates at all (a name, a
    # title, an error message); fetch their representative item and add them.
    missing = [
        media_id
        for media_id in coverage
        if media_id not in by_media
    ]
    if missing:
        for media_id, row in db.first_items_for_media(missing).items():
            if kind in ("image", "video") and row["kind"] != kind:
                continue
            by_media[media_id] = _entry(int(row["item_id"]), media_id, row, 0.0)

    for media_id, entry in by_media.items():
        text_score = coverage.get(media_id, 0.0)
        entry["text_score"] = round(text_score, 3)
        entry["clip_score"] = entry["score"]
        entry["score"] = round(
            max(entry["score"], config.OCR_BOOST * text_score), 4
        )

    ranked = sorted(by_media.values(), key=lambda r: r["score"], reverse=True)

    def strong(entry: dict[str, Any]) -> bool:
        if cutoff is None:
            return True
        return (
            entry["score"] >= cutoff
            or entry["text_score"] >= config.OCR_STRONG_COVERAGE
        )

    strong_hits = [entry for entry in ranked if strong(entry)]
    for entry in ranked:
        entry["strong"] = strong(entry)
        entry["moments"].sort(key=lambda moment: (moment["t"] is None, moment["t"] or 0.0))

    if show_all:
        results = ranked[:limit]
    elif strong_hits:
        results = strong_hits[:limit]
    else:
        results = ranked[:GUESSES_WHEN_WEAK]

    t6 = time.perf_counter()
    return {
        "query": query,
        "results": results,
        "candidates": len(ids) + len(missing),
        "strong_count": len(strong_hits),
        "weak_count": max(0, len(ranked) - len(strong_hits)),
        "weak_only": bool(results) and not strong_hits,
        "cutoff": round(cutoff, 4) if cutoff is not None else None,
        "cutoff_z": cutoff_z,
        "show_all": show_all,
        "text_matches": len(coverage),
        "took_ms": round((t6 - t0) * 1000, 2),
        "timings": {
            "embed_ms": round((t1 - t0) * 1000, 2),
            "faiss_ms": round((t2 - t1) * 1000, 2),
            "calibrate_ms": round((t3 - t2) * 1000, 2),
            "db_ms": round((t4 - t3) * 1000, 2),
            "lexical_ms": round((t5 - t4) * 1000, 2),
            "group_ms": round((t6 - t5) * 1000, 2),
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
