"""Open the library consistently for the API and the CLI.

SQLite stores the embeddings, FAISS is the search cache. If the cache is
missing or out of date it is rebuilt from SQLite without touching the model.
Only a different embedding model (or an older data layout) forces a re-embed.
"""

from __future__ import annotations

from . import config, db
from .embedder import ClipEmbedder
from .index import VectorIndex, get_index


def open_library(embedder: ClipEmbedder) -> VectorIndex:
    embedder.load()

    try:
        index = get_index(dim=embedder.dim)
    except Exception as exc:
        # Corrupt or incompatible cache file: rebuild from SQLite below.
        print(f"[photo-finder] index cache unusable ({type(exc).__name__}): rebuilding")
        config.INDEX_PATH.unlink(missing_ok=True)
        index = get_index(dim=embedder.dim)

    stats = db.stats()
    fingerprint = db.fingerprint()
    stored = db.get_meta("fingerprint")

    if stored != fingerprint or db.count_vectors() != stats["items"]:
        db.clear_library()
        index.reset(embedder.dim)
        print("[photo-finder] embedding model changed: library will be re-indexed")
    elif index.count != stats["items"]:
        rebuilt = index.rebuild(db.iter_vectors(), embedder.dim)
        print(f"[photo-finder] rebuilt FAISS index from SQLite: {rebuilt} vectors")

    db.set_meta("fingerprint", fingerprint)
    return index
