"""Core tests that do not need CLIP weights or ffmpeg.

The embedder is replaced with a 4-dimensional stub; what is under test is the
metadata store, the FAISS wrapper, grouping and index recovery.
"""

from __future__ import annotations

import numpy as np
import pytest

from photofinder import db
from photofinder.index import VectorIndex
from photofinder.search import search


class StubEmbedder:
    dim = 4

    def embed_text(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


def unit(*values: float) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


@pytest.fixture()
def library(tmp_path):
    db.init()
    db.clear_library()
    index = VectorIndex(path=tmp_path / "index.faiss", dim=4)

    media = {}
    rows = [
        ("C:/lib/beach.jpg", "image", [1.0, 0.02, 0.0, 0.0]),
        ("C:/lib/holiday.mp4", "video", [0.9, 0.4, 0.0, 0.0]),
        ("C:/lib/beach_2.jpg", "image", [0.5, 0.0, 0.5, 0.0]),
    ]
    for path, kind, vector in rows:
        media_id = db.upsert_media(
            path=path,
            kind=kind,
            size=10,
            mtime=1.0,
            width=100,
            height=100,
            duration=12.0 if kind == "video" else None,
            taken_at=None,
        )
        media[path] = media_id
        if kind == "image":
            times = [None]
        else:
            times = [6.0, 0.0, 4.0]
        items = [
            {"t": t, "thumb": f"{media_id}_{n}.jpg", "vector": unit(*vector).tobytes()}
            for n, t in enumerate(times)
        ]
        ids = db.add_items(media_id, items)
        index.add(ids, np.stack([unit(*vector)] * len(ids)))

    yield media, index
    db.clear_library()
    index.reset(4)


def test_search_groups_video_moments_and_sorts_them(library):
    media, index = library
    result = search("beach", limit=10, index=index, embedder=StubEmbedder())

    assert result["results"], "expected results"
    assert result["results"][0]["path"] == "C:/lib/beach.jpg"
    video = next(item for item in result["results"] if item["kind"] == "video")
    assert video["media_id"] == media["C:/lib/holiday.mp4"]
    assert len(video["moments"]) == 3
    assert [moment["t"] for moment in video["moments"]] == [0.0, 4.0, 6.0]
    assert result["timings"].keys() == {"embed_ms", "faiss_ms", "db_ms", "group_ms"}
    assert result["took_ms"] >= 0
    assert result["candidates"] == 5


def test_kind_filter(library):
    _, index = library
    result = search("beach", limit=10, kind="image", index=index, embedder=StubEmbedder())
    assert {item["kind"] for item in result["results"]} == {"image"}


def test_removed_media_disappears(library):
    media, index = library
    media_id = media["C:/lib/beach.jpg"]
    ids = db.delete_items_for_media(media_id)
    index.remove(ids)
    result = search("beach", limit=10, index=index, embedder=StubEmbedder())
    assert all(item["path"] != "C:/lib/beach.jpg" for item in result["results"])
    assert index.count == 4


def test_faiss_rebuilds_from_sqlite(library, tmp_path):
    _, index = library
    assert index.count == 5
    rebuilt = VectorIndex(path=tmp_path / "rebuilt.faiss", dim=4)
    count = rebuilt.rebuild(db.iter_vectors(), 4)
    assert count == 5
    assert rebuilt.count == 5

    result = search("beach", limit=10, index=rebuilt, embedder=StubEmbedder())
    assert result["results"][0]["path"] == "C:/lib/beach.jpg"


def test_recent_returns_one_representative_moment(library):
    _, _index = library
    rows = db.recent(limit=10)
    assert {row["path"] for row in rows} == {
        "C:/lib/beach.jpg",
        "C:/lib/holiday.mp4",
        "C:/lib/beach_2.jpg",
    }
    video = next(row for row in rows if row["kind"] == "video")
    assert video["t"] == 6.0  # MIN(id) row for that video


def test_media_under_does_not_match_a_sibling_prefix(library):
    _, _index = library
    media_id = db.upsert_media(
        path="C:/library/other.jpg",
        kind="image",
        size=10,
        mtime=1.0,
        width=10,
        height=10,
        duration=None,
        taken_at=None,
    )
    db.add_items(media_id, [{"t": None, "thumb": "x.jpg", "vector": unit(0, 1, 0, 0).tobytes()}])

    under = db.media_under("C:/lib")
    assert "C:/lib/beach.jpg" in under
    assert "C:/library/other.jpg" not in under
