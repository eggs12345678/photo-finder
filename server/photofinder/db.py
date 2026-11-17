"""SQLite metadata store.

One row per media file plus one row per searchable vector (``items``). The
FAISS row id is ``items.id``, which keeps the index and metadata in sync even
after deletions.
"""

from __future__ import annotations

import numpy as np
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roots (
    path     TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL UNIQUE,
    kind      TEXT NOT NULL,
    size      INTEGER NOT NULL,
    mtime     REAL NOT NULL,
    width     INTEGER,
    height    INTEGER,
    duration  REAL,
    taken_at  TEXT,
    status    TEXT NOT NULL DEFAULT 'ready',
    error     TEXT,
    indexed_at TEXT,
    ocr_text  TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id       INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    t        REAL,
    thumb    TEXT NOT NULL,
    vector   BLOB
);
CREATE INDEX IF NOT EXISTS idx_items_media ON items(media_id);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
    text,
    media_id UNINDEXED,
    tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_vocab USING fts5vocab(ocr_fts, 'row');
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
    if "vector" not in columns:
        conn.execute("ALTER TABLE items ADD COLUMN vector BLOB")
    media_columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    if "ocr_text" not in media_columns:
        conn.execute("ALTER TABLE media ADD COLUMN ocr_text TEXT")
    conn.commit()


# --- meta -------------------------------------------------------------------

def get_meta(key: str, default: str | None = None) -> str | None:
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def fingerprint() -> str:
    # The preprocessing is part of the embedding space: changing between CLIP's
    # centre crop and the full-frame squash makes old vectors incomparable, so
    # it must force a re-index just like a model change.
    preprocess = "squash" if config.EMBED_SQUASH else "crop"
    return f"{config.MODEL_NAME}/{config.MODEL_PRETRAINED}/{preprocess}"


# --- roots ------------------------------------------------------------------

def add_root(path: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT OR IGNORE INTO roots(path, added_at) VALUES(?,?)",
        (path, now_iso()),
    )
    conn.commit()


def roots() -> list[str]:
    return [r["path"] for r in connect().execute("SELECT path FROM roots ORDER BY added_at")]


def remove_root(path: str) -> None:
    conn = connect()
    conn.execute("DELETE FROM roots WHERE path=?", (path,))
    conn.commit()


# --- media ------------------------------------------------------------------

def media_by_path(path: str) -> sqlite3.Row | None:
    return connect().execute("SELECT * FROM media WHERE path=?", (path,)).fetchone()


def upsert_media(
    *,
    path: str,
    kind: str,
    size: int,
    mtime: float,
    width: int | None,
    height: int | None,
    duration: float | None,
    taken_at: str | None,
    status: str = "ready",
    error: str | None = None,
) -> int:
    conn = connect()
    row = media_by_path(path)
    if row is None:
        cur = conn.execute(
            "INSERT INTO media(path,kind,size,mtime,width,height,duration,taken_at,status,error,indexed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (path, kind, size, mtime, width, height, duration, taken_at, status, error, now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    conn.execute(
        "UPDATE media SET kind=?,size=?,mtime=?,width=?,height=?,duration=?,taken_at=?,"
        "status=?,error=?,indexed_at=? WHERE id=?",
        (
            kind, size, mtime, width, height, duration, taken_at,
            status, error, now_iso(), row["id"],
        ),
    )
    conn.commit()
    return int(row["id"])


def delete_items_for_media(media_id: int) -> list[int]:
    conn = connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM items WHERE media_id=?", (media_id,))]
    if ids:
        conn.executemany("DELETE FROM items WHERE id=?", [(i,) for i in ids])
        conn.commit()
    return ids


def delete_media(media_id: int) -> list[int]:
    ids = delete_items_for_media(media_id)
    conn = connect()
    conn.execute("DELETE FROM ocr_fts WHERE media_id=?", (media_id,))
    conn.execute("DELETE FROM media WHERE id=?", (media_id,))
    conn.commit()
    return ids


def thumbs_for_media(media_id: int) -> list[str]:
    return [
        r["thumb"]
        for r in connect().execute("SELECT thumb FROM items WHERE media_id=?", (media_id,))
    ]


def delete_thumb_files(names: Iterable[str]) -> None:
    for name in names:
        try:
            (config.THUMB_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass


def set_media_error(media_id: int, error: str) -> None:
    conn = connect()
    conn.execute("UPDATE media SET status='error', error=? WHERE id=?", (error[:500], media_id))
    conn.commit()


def add_items(media_id: int, items: Iterable[dict[str, Any]]) -> list[int]:
    """Insert items; returns the new row ids in the same order.

    Each item carries its embedding so the FAISS index is a derived cache that
    can be rebuilt exactly after a crash or a model-free restart.
    """
    conn = connect()
    ids: list[int] = []
    for item in items:
        cur = conn.execute(
            "INSERT INTO items(media_id,t,thumb,vector) VALUES(?,?,?,?)",
            (media_id, item.get("t"), item["thumb"], item.get("vector")),
        )
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids


def count_vectors() -> int:
    return int(
        connect().execute("SELECT COUNT(*) AS n FROM items WHERE vector IS NOT NULL").fetchone()["n"]
    )


def iter_vectors() -> Iterable[tuple[int, bytes]]:
    cursor = connect().execute("SELECT id, vector FROM items WHERE vector IS NOT NULL ORDER BY id")
    for row in cursor:
        yield int(row["id"]), row["vector"]


def sample_vectors(limit: int = 2048) -> np.ndarray:
    """A random spread of stored vectors, used to calibrate confidence.

    Ordering by RANDOM() is fine here: it runs once per index version over an
    indexed table, and 2048 rows is a few milliseconds even at a million items.
    """
    rows = connect().execute(
        "SELECT vector FROM items WHERE vector IS NOT NULL ORDER BY RANDOM() LIMIT ?",
        (int(limit),),
    ).fetchall()
    if not rows:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = [np.frombuffer(row["vector"], dtype=np.float32) for row in rows]
    width = vectors[0].size
    vectors = [v for v in vectors if v.size == width]
    return np.stack(vectors)


def items_with_media(ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    conn = connect()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT i.id AS item_id, i.t, i.thumb, m.id AS media_id, m.path, m.kind, "
        "m.width, m.height, m.duration, m.taken_at, m.mtime, m.ocr_text "
        f"FROM items i JOIN media m ON m.id=i.media_id "
        f"WHERE i.id IN ({marks}) AND m.status='ready'",
        ids,
    ).fetchall()
    return {int(r["item_id"]): r for r in rows}


def first_items_for_media(media_ids: list[int]) -> dict[int, sqlite3.Row]:
    """One representative item per media, for hits found through OCR text."""
    if not media_ids:
        return {}
    marks = ",".join("?" * len(media_ids))
    rows = connect().execute(
        "SELECT i.id AS item_id, i.t, i.thumb, m.id AS media_id, m.path, m.kind, "
        "m.width, m.height, m.duration, m.taken_at, m.mtime, m.ocr_text "
        "FROM items i JOIN media m ON m.id=i.media_id "
        f"WHERE i.id IN (SELECT MIN(id) FROM items WHERE media_id IN ({marks}) GROUP BY media_id) "
        "AND m.status='ready'",
        media_ids,
    ).fetchall()
    return {int(r["media_id"]): r for r in rows}


# --- OCR text ---------------------------------------------------------------

def set_ocr_text(media_id: int, text: str | None) -> None:
    """Store the text found in a media file ("" means "processed, no text").

    Passing None leaves the column untouched, so indexing without an OCR
    engine never erases text that is already there.
    """
    if text is None:
        return
    conn = connect()
    conn.execute("DELETE FROM ocr_fts WHERE media_id=?", (media_id,))
    conn.execute("UPDATE media SET ocr_text=? WHERE id=?", (text, media_id))
    if text:
        conn.execute(
            "INSERT INTO ocr_fts(text, media_id) VALUES(?,?)",
            (text, media_id),
        )
    conn.commit()


def ocr_postings(tokens: list[str]) -> tuple[dict[str, set[int]], dict[str, float], int]:
    """Inverted index for OCR text: token -> media ids, with IDF weights.

    Tokens that appear in more than ``OCR_STOPWORD_RATIO`` of the documents are
    dropped, both from the numerator and the denominator, so a query made of
    common words cannot look like a perfect text match.
    """
    conn = connect()
    total = int(conn.execute("SELECT COUNT(*) AS n FROM ocr_fts").fetchone()["n"])
    if not total or not tokens:
        return {}, {}, total
    marks = ",".join("?" * len(tokens))
    counts = {
        row["term"]: int(row["doc"])
        for row in conn.execute(
            f"SELECT term, doc FROM ocr_vocab WHERE term IN ({marks})", tokens
        )
    }
    limit = max(1.0, config.OCR_STOPWORD_RATIO * total)
    if total < 8:
        # With a handful of documents, "common" is meaningless; keep every word.
        kept = list(tokens)
    else:
        kept = [token for token in tokens if counts.get(token, 0) < limit]
    postings: dict[str, set[int]] = {}
    for token in kept:
        rows = conn.execute(
            "SELECT media_id FROM ocr_fts WHERE ocr_fts MATCH ?", (f'"{token}"',)
        ).fetchall()
        postings[token] = {int(row["media_id"]) for row in rows}
    idf = {token: math.log(1 + total / (1 + counts.get(token, 0))) for token in kept}
    return postings, idf, total


def ocr_stats() -> dict[str, int]:
    conn = connect()
    documents = conn.execute("SELECT COUNT(*) AS n FROM ocr_fts").fetchone()["n"]
    processed = conn.execute(
        "SELECT COUNT(*) AS n FROM media WHERE ocr_text IS NOT NULL"
    ).fetchone()["n"]
    return {"documents": int(documents), "processed": int(processed)}


def thumb_for_item(item_id: int) -> str | None:
    row = connect().execute("SELECT thumb FROM items WHERE id=?", (item_id,)).fetchone()
    return row["thumb"] if row else None


def media_by_id(media_id: int) -> sqlite3.Row | None:
    return connect().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()


def clear_library() -> None:
    conn = connect()
    conn.execute("DELETE FROM ocr_fts")
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM media")
    conn.commit()


# --- stats / browsing -------------------------------------------------------

def stats() -> dict[str, Any]:
    conn = connect()
    media = conn.execute(
        "SELECT kind, COUNT(*) AS n, SUM(size) AS bytes, MAX(indexed_at) AS last "
        "FROM media WHERE status='ready' GROUP BY kind"
    ).fetchall()
    items = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    errors = conn.execute("SELECT COUNT(*) AS n FROM media WHERE status='error'").fetchone()["n"]
    by_kind = {r["kind"]: int(r["n"]) for r in media}
    return {
        "images": by_kind.get("image", 0),
        "videos": by_kind.get("video", 0),
        "items": int(items),
        "errors": int(errors),
        "bytes": int(sum(r["bytes"] or 0 for r in media)),
        "last_indexed": max((r["last"] for r in media if r["last"]), default=None),
        "model": fingerprint(),
    }


def recent(limit: int = 24, kind: str | None = None) -> list[dict[str, Any]]:
    """Most recently indexed media, one representative moment each."""
    conn = connect()
    where = ["m.status='ready'"]
    args: list[Any] = []
    if kind in ("image", "video"):
        where.append("m.kind=?")
        args.append(kind)
    rows = conn.execute(
        "SELECT m.id AS media_id, m.path, m.kind, m.duration, m.taken_at, m.width, m.height, "
        "m.mtime, i.id AS item_id, i.t, i.thumb "
        "FROM media m JOIN items i ON i.id = ("
        "  SELECT MIN(id) FROM items WHERE media_id = m.id"
        ") "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY m.indexed_at DESC, m.id DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def media_under(prefix: str) -> list[str]:
    """Media files under a folder, using path-aware comparison (no C:\\Photos2)."""
    root = os.path.normcase(prefix.rstrip("\\/"))
    if not root:
        return []
    matches: list[str] = []
    for row in connect().execute("SELECT path FROM media"):
        path = os.path.normcase(row["path"])
        if path.startswith(root + "\\") or path.startswith(root + "/"):
            matches.append(row["path"])
    return matches
