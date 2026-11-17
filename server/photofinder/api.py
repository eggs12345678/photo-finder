"""FastAPI application: local search API plus the built web UI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, search as search_mod
from .bootstrap import open_library
from .embedder import get_embedder
from .ingest import IngestManager

BOOT: dict[str, Any] = {"state": "loading", "error": None, "started_at": time.time()}
manager = IngestManager()
_boot_lock = threading.Lock()


def _boot() -> None:
    try:
        config.ensure_dirs()
        db.init()
        embedder = get_embedder()
        index = open_library(embedder)

        manager.index = index
        BOOT.update(state="ready", error=None, finished_at=time.time())
        for root in db.roots():
            if Path(root).exists():
                manager.enqueue(root)
    except Exception as exc:  # surfaced through /api/health
        BOOT.update(state="error", error=f"{type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    db.init()
    manager.start()
    threading.Thread(target=_boot, name="boot", daemon=True).start()
    yield
    manager.shutdown()


app = FastAPI(title="photo-finder", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class IndexRequest(BaseModel):
    path: str
    full: bool = False


class PathRequest(BaseModel):
    path: str


def _require_ready() -> None:
    if BOOT["state"] == "error":
        raise HTTPException(500, f"Startup failed: {BOOT['error']}")
    if BOOT["state"] != "ready" or manager.index is None:
        raise HTTPException(503, "Model is still loading")


@app.get("/api/health")
def health() -> dict[str, Any]:
    stats = {"images": 0, "videos": 0, "items": 0}
    try:
        stats = db.stats()
    except Exception:
        pass
    return {
        "ok": BOOT["state"] == "ready",
        "boot": BOOT["state"],
        "error": BOOT["error"],
        "model": config.MODEL_NAME,
        "pretrained": config.MODEL_PRETRAINED,
        "dim": manager.index.dim if manager.index else None,
        "vectors": manager.index.count if manager.index else 0,
        "library": stats,
        "data_dir": str(config.DATA_DIR),
    }


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    payload: dict[str, Any] = {"roots": db.roots(), "boot": BOOT["state"]}
    try:
        payload.update(db.stats())
        payload["vectors"] = manager.index.count if manager.index else 0
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {"ingest": manager.status(), "boot": BOOT["state"], "error": BOOT["error"]}


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(config.DEFAULT_LIMIT, ge=1, le=200),
    kind: str | None = Query(None, pattern="^(image|video)$"),
    all: bool = Query(False, description="Include weak matches below the confidence cutoff"),
) -> dict[str, Any]:
    _require_ready()
    return search_mod.search(q, limit=limit, kind=kind, index=manager.index, show_all=all)


@app.get("/api/recent")
def api_recent(
    limit: int = Query(24, ge=1, le=100),
    kind: str | None = Query(None, pattern="^(image|video)$"),
) -> dict[str, Any]:
    items = []
    for row in db.recent(limit=limit, kind=kind):
        items.append(
            {
                "media_id": row["media_id"],
                "item_id": row["item_id"],
                "path": row["path"],
                "name": Path(row["path"]).name,
                "kind": row["kind"],
                "t": row["t"],
                "duration": row["duration"],
                "taken_at": row["taken_at"],
                "mtime": row["mtime"],
                "thumb": f"/api/thumb/{row['item_id']}",
                "media_url": f"/api/media/{row['media_id']}",
            }
        )
    return {"results": items}


@app.post("/api/index")
def api_index(body: IndexRequest) -> dict[str, Any]:
    _require_ready()
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(404, f"Path not found: {path}")
    queued = manager.enqueue(str(path.resolve()), full=body.full)
    return {"queued": queued, "status": manager.status()}


@app.post("/api/rescan")
def api_rescan(full: bool = False) -> dict[str, Any]:
    _require_ready()
    queued = 0
    for root in db.roots():
        if Path(root).exists() and manager.enqueue(root, full=full):
            queued += 1
    return {"queued": queued, "status": manager.status()}


@app.post("/api/cancel")
def api_cancel() -> dict[str, Any]:
    manager.cancel()
    return {"status": manager.status()}


@app.delete("/api/roots")
def api_remove_root(path: str = Query(...)) -> dict[str, Any]:
    _require_ready()
    root = str(Path(path).expanduser().resolve())
    known = {os.path.normcase(str(Path(p).resolve())) for p in db.roots()}
    if os.path.normcase(root) not in known:
        raise HTTPException(404, "Unknown root")
    removed = 0
    for media_path in db.media_under(root):
        row = db.media_by_path(media_path)
        if row is None:
            continue
        thumbs = db.thumbs_for_media(int(row["id"]))
        ids = db.delete_media(int(row["id"]))
        if ids and manager.index:
            manager.index.remove(ids)
        db.delete_thumb_files(thumbs)
        removed += 1
    if manager.index:
        manager.index.save()
    db.remove_root(root)
    return {"removed": removed}


@app.get("/api/thumb/{item_id}")
def api_thumb(item_id: int) -> FileResponse:
    thumb = db.thumb_for_item(item_id)
    if not thumb:
        raise HTTPException(404, "Thumbnail not found")
    path = config.THUMB_DIR / thumb
    if not path.exists():
        raise HTTPException(404, "Thumbnail file missing")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/media/{media_id}")
def api_media(media_id: int) -> FileResponse:
    row = db.media_by_id(media_id)
    if row is None:
        raise HTTPException(404, "Media not found")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(410, "File moved or deleted")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.post("/api/reveal")
def api_reveal(body: PathRequest) -> dict[str, Any]:
    row = db.media_by_path(str(Path(body.path).expanduser().resolve()))
    if row is None:
        raise HTTPException(404, "Not in library")
    path = Path(row["path"])
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
    return {"ok": True}


@app.post("/api/open")
def api_open(body: PathRequest) -> dict[str, Any]:
    row = db.media_by_path(str(Path(body.path).expanduser().resolve()))
    if row is None:
        raise HTTPException(404, "Not in library")
    path = Path(row["path"])
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"ok": True}


# --- static UI --------------------------------------------------------------

_web_dir = config.PROJECT_DIR / "web" / "dist"
if _web_dir.exists():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
else:

    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "photo-finder",
                "hint": "API is running. Build the UI with `npm install && npm run build` in web/, "
                "or run `npm run dev` for the dev server.",
                "api": "/api/health",
            }
        )
