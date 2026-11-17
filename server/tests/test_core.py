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
    assert result["timings"].keys() == {
        "embed_ms",
        "faiss_ms",
        "calibrate_ms",
        "db_ms",
        "group_ms",
    }
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


class EnsembleEmbedder:
    """Stub that records the query and only implements the ensemble entry point."""

    dim = 4

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_text(self, text: str) -> np.ndarray:  # pragma: no cover - must not be used
        raise AssertionError("search should prefer embed_query")

    def embed_query(self, query: str) -> np.ndarray:
        self.queries.append(query)
        return unit(1.0, 0.02, 0.0, 0.0)


def test_search_uses_query_ensemble(library):
    _, index = library
    embedder = EnsembleEmbedder()

    result = search("beach", limit=10, index=index, embedder=embedder)

    assert embedder.queries == ["beach"]
    assert result["results"][0]["path"] == "C:/lib/beach.jpg"


def test_embed_query_averages_prompts(monkeypatch):
    from photofinder import config
    from photofinder.embedder import ClipEmbedder

    embedder = ClipEmbedder()
    seen: list[str] = []

    def fake_embed_texts(texts: list[str]) -> np.ndarray:
        seen.extend(texts)
        return np.stack([unit(1.0, 0.0, 0.0, 0.0) for _ in texts])

    monkeypatch.setattr(embedder, "embed_texts", fake_embed_texts)

    vector = embedder.embed_query("dog")

    assert seen == [template.format(query="dog") for template in config.QUERY_PROMPTS]
    assert np.allclose(np.linalg.norm(vector), 1.0)
    assert np.allclose(vector, unit(1.0, 0.0, 0.0, 0.0))


@pytest.fixture()
def crowded_library(tmp_path):
    """40 vectors so calibration has a usable null distribution.

    One near-perfect match against the stub query; 39 distractors sit at a flat
    0.2 similarity, which gives the sample a real mean and standard deviation.
    """
    db.init()
    db.clear_library()
    index = VectorIndex(path=tmp_path / "crowded.faiss", dim=4)
    index.reset(4)

    query = unit(1.0, 0.02, 0.0, 0.0)
    distractor = unit(0.2, np.sqrt(0.96), 0.0, 0.0)
    vectors = [query] + [distractor] * 39
    for n, vector in enumerate(vectors):
        media_id = db.upsert_media(
            path=f"C:/crowd/{n:02d}.jpg",
            kind="image",
            size=10,
            mtime=1.0,
            width=100,
            height=100,
            duration=None,
            taken_at=None,
        )
        ids = db.add_items(media_id, [{"t": None, "thumb": f"c{n}.jpg", "vector": vector.tobytes()}])
        index.add(ids, np.stack([vector]))

    yield index
    db.clear_library()
    index.reset(4)


def test_confidence_gating_hides_weak_matches(crowded_library):
    result = search("beach", limit=10, index=crowded_library, embedder=EnsembleEmbedder())

    assert result["strong_count"] == 1
    assert result["weak_count"] == 39
    assert [item["path"] for item in result["results"]] == ["C:/crowd/00.jpg"]
    assert result["results"][0]["strong"] is True
    assert result["cutoff"] is not None
    assert result["weak_only"] is False


def test_show_all_includes_weak_matches(crowded_library):
    result = search(
        "beach",
        limit=10,
        index=crowded_library,
        embedder=EnsembleEmbedder(),
        show_all=True,
    )

    assert result["show_all"] is True
    assert len(result["results"]) == 10
    assert sum(1 for item in result["results"] if item["strong"]) == 1
    assert result["weak_count"] == 39


def test_weak_only_returns_a_few_guesses(crowded_library, monkeypatch):
    from photofinder import config

    monkeypatch.setattr(config, "STRICT_Z", 50.0)

    result = search("beach", limit=10, index=crowded_library, embedder=EnsembleEmbedder())

    assert result["strong_count"] == 0
    assert result["weak_only"] is True
    assert len(result["results"]) == 3
    assert all(item["strong"] is False for item in result["results"])


def test_flat_distribution_is_not_all_strong(tmp_path):
    """Copies of one image have zero spread; nothing should look confident."""
    db.init()
    db.clear_library()
    index = VectorIndex(path=tmp_path / "flat.faiss", dim=4)
    index.reset(4)

    vector = unit(0.3, np.sqrt(1 - 0.09), 0.0, 0.0)
    for n in range(20):
        media_id = db.upsert_media(
            path=f"C:/flat/{n:02d}.jpg",
            kind="image",
            size=10,
            mtime=1.0,
            width=100,
            height=100,
            duration=None,
            taken_at=None,
        )
        ids = db.add_items(
            media_id, [{"t": None, "thumb": f"f{n}.jpg", "vector": vector.tobytes()}]
        )
        index.add(ids, np.stack([vector]))

    try:
        result = search("beach", limit=10, index=index, embedder=EnsembleEmbedder())
        assert result["strong_count"] == 0
        assert result["weak_only"] is True
        assert len(result["results"]) == 3
    finally:
        db.clear_library()
        index.reset(4)
