"""Text-to-media search over the FAISS index."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import db
from .embedder import get_embedder
from .index import get_index

MOMENTS_PER_RESULT = 4


def search(
    query: str,
    limit: int = 60,
    kind: str | None = None,
    index=None,
    embedder=None,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"query": query, "results": [], "took_ms": 0.0, "timings": {}, "candidates": 0}

    embedder = embedder or get_embedder()
    index = index or get_index(dim=embedder.dim)

    t0 = time.perf_counter()
    vector = embedder.embed_text(query)
    t1 = time.perf_counter()

    # Over-fetch so grouping video moments into one result does not starve the
    # page, then trim to the requested number of distinct files. A type filter
    # needs a wider net because the filter is applied after the vector search.
    multiplier = 12 if kind is None else 40
    k = min(max(limit * multiplier, 200), 5000, max(index.count, 1))
    ids, scores = index.search(vector, k)
    t2 = time.perf_counter()

    rows = db.items_with_media([int(i) for i in ids])
    t3 = time.perf_counter()

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

    results = sorted(by_media.values(), key=lambda r: r["score"], reverse=True)[:limit]
    for entry in results:
        entry["moments"].sort(key=lambda moment: (moment["t"] is None, moment["t"] or 0.0))
    t4 = time.perf_counter()
    return {
        "query": query,
        "results": results,
        "candidates": len(ids),
        "took_ms": round((t4 - t0) * 1000, 2),
        "timings": {
            "embed_ms": round((t1 - t0) * 1000, 2),
            "faiss_ms": round((t2 - t1) * 1000, 2),
            "db_ms": round((t3 - t2) * 1000, 2),
            "group_ms": round((t4 - t3) * 1000, 2),
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
