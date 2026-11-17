"""Scanning, thumbnail generation and embedding.

Images become one vector each. Videos are sampled into frames (one every few
seconds, near-duplicates dropped) and every kept frame is a searchable moment
with its own timestamp and thumbnail.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

try:  # iPhone / AVIF support when the optional decoder is installed
    import pillow_heif

    pillow_heif.register_heif_opener()
    pillow_heif.register_avif_opener()
except Exception:  # pragma: no cover - Pillow handles the formats it knows
    pass

from . import config, db, ocr
from .embedder import ClipEmbedder, get_embedder
from .index import VectorIndex, get_index

SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", "$recycle.bin",
    "system volume information", ".thumbnails", ".cache", "venv", ".venv",
}


def _is_supported(path: Path) -> tuple[str | None, str | None]:
    ext = path.suffix.lower()
    if ext in config.IMAGE_EXTS:
        return "image", ext
    if ext in config.VIDEO_EXTS:
        return "video", ext
    return None, None


def _walk(root: Path) -> Iterable[Path]:
    data_dir = config.DATA_DIR.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d.lower() not in SKIP_DIRS
            and not d.startswith(".")
            and (current / d).resolve() != data_dir
        ]
        for name in filenames:
            yield current / name


def _exif_taken(im: Image.Image) -> str | None:
    try:
        exif = im.getexif()
        raw = None
        try:
            raw = exif.get_ifd(0x8769).get(36867)  # DateTimeOriginal
        except Exception:
            raw = None
        raw = raw or exif.get(306)  # DateTime
        if not raw:
            return None
        return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        return None


def _load_image(path: Path) -> tuple[Image.Image, int, int, str | None]:
    with Image.open(path) as im:
        taken = _exif_taken(im)
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        im.thumbnail((config.EXTRACT_WIDTH, config.EXTRACT_WIDTH), Image.Resampling.LANCZOS)
        rgb = im.convert("RGB")
    return rgb, width, height, taken


def _save_thumb(image: Image.Image, key: str) -> str:
    name = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:24] + ".jpg"
    target = config.THUMB_DIR / name
    if not target.exists():
        thumb = image.copy()
        thumb.thumbnail((config.THUMB_SIZE, config.THUMB_SIZE), Image.Resampling.LANCZOS)
        thumb.save(target, "JPEG", quality=82, optimize=True, progressive=True)
    return name


@dataclass
class Job:
    root: str
    full: bool = False


@dataclass
class Progress:
    state: str = "idle"  # idle | scanning | indexing | done | error | cancelled
    root: str | None = None
    phase: str | None = None
    total: int = 0
    done: int = 0
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    added_items: int = 0
    current: str | None = None
    message: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    queued: int = 0
    _per_second: float = 0.0
    _done_at_start: int = field(default=0, repr=False)

    def as_dict(self) -> dict[str, Any]:
        rate = self._per_second
        eta = None
        if rate > 0 and self.total > self.done:
            eta = round((self.total - self.done) / rate, 1)
        return {
            "state": self.state,
            "root": self.root,
            "phase": self.phase,
            "total": self.total,
            "done": self.done,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "errors": self.errors,
            "added_items": self.added_items,
            "current": self.current,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "queued": self.queued,
            "rate": round(rate, 2),
            "eta_seconds": eta,
        }


class IngestManager:
    def __init__(self, embedder: ClipEmbedder | None = None, index: VectorIndex | None = None) -> None:
        self.embedder = embedder or get_embedder()
        self.index = index
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = Progress()
        self._dirty = False
        self._force = False

    # -- lifecycle -----------------------------------------------------------

    def _ensure_index(self) -> VectorIndex:
        if self.index is None:
            self.embedder.load()
            self.index = get_index(dim=self.embedder.dim)
        return self.index

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ingest", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=10)
        if self._dirty:
            self._save_index(force=True)

    def enqueue(self, root: str, full: bool = False) -> bool:
        root = str(Path(root).expanduser().resolve())
        with self._lock:
            if root in self._pending:
                return False
            self._pending.add(root)
            self._queue.put(Job(root=root, full=full))
            self._progress.queued = len(self._pending)
        return True

    def cancel(self) -> None:
        self._cancel.set()

    # -- status --------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            p = self._progress
            p.queued = len(self._pending)
            if p.state in ("scanning", "indexing") and p.done:
                elapsed = max(time.time() - (p.started_at or time.time()), 0.001)
                p._per_second = (p.done - p._done_at_start) / elapsed
            return p.as_dict()

    def _update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._progress, key, value)

    def _bump(self, **deltas: int) -> None:
        with self._lock:
            for key, value in deltas.items():
                setattr(self._progress, key, getattr(self._progress, key) + value)

    # -- worker --------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                self._run_job(job)
            except Exception as exc:  # keep the worker alive
                self._update(state="error", message=f"{type(exc).__name__}: {exc}", finished_at=time.time())
            finally:
                with self._lock:
                    self._pending.discard(job.root)
                    self._progress.queued = len(self._pending)
                self._queue.task_done()

    def _run_job(self, job: Job) -> None:
        root = Path(job.root)
        self._cancel.clear()
        self._dirty = False
        self._force = job.full
        self._update(
            state="scanning",
            root=str(root),
            phase="scan",
            total=0,
            done=0,
            indexed=0,
            skipped=0,
            errors=0,
            added_items=0,
            current=None,
            message=None,
            started_at=time.time(),
            finished_at=None,
            _per_second=0.0,
            _done_at_start=0,
        )
        if not root.exists():
            self._update(state="error", message=f"Path does not exist: {root}", finished_at=time.time())
            return

        if root.is_dir():
            db.add_root(str(root))
            files = [p for p in _walk(root) if _is_supported(p)[0]]
        else:
            if not _is_supported(root)[0]:
                self._update(
                    state="error",
                    message="Unsupported file type",
                    finished_at=time.time(),
                )
                return
            files = [root]
        self._update(total=len(files), phase="scan")

        self._ensure_index()
        seen: set[str] = set()
        batch: list[tuple[Path, os.stat_result, Image.Image, int, int, str | None, str | None]] = []
        stop_scan = False

        for path in files:
            if self._cancel.is_set():
                stop_scan = True
                break
            kind, _ = _is_supported(path)
            if not kind:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            path_str = str(path)
            seen.add(path_str)
            self._update(current=path.name)
            if self._is_fresh(path_str, st):
                self._bump(done=1, skipped=1)
                continue

            if kind == "image":
                try:
                    image, width, height, taken = _load_image(path)
                except Exception as exc:
                    self._record_error(path, st, f"image: {type(exc).__name__}: {exc}")
                    self._bump(done=1, errors=1)
                    continue
                batch.append((path, st, image, width, height, taken, ocr.extract(path)))
                if len(batch) >= config.EMBED_BATCH:
                    self._flush_images(batch)
                    batch = []
            else:
                if batch:
                    self._flush_images(batch)
                    batch = []
                self._index_video(path, st)
        if batch:
            self._flush_images(batch)

        if not stop_scan and not self._cancel.is_set() and root.is_dir():
            self._update(phase="cleanup")
            removed = self._remove_missing(str(root), seen)
            if removed:
                self._update(message=f"Removed {removed} missing file(s)")

        if self._dirty:
            self._save_index(force=True)
        state = "cancelled" if stop_scan or self._cancel.is_set() else "done"
        self._update(state=state, phase=None, current=None, finished_at=time.time())

    # -- images --------------------------------------------------------------

    def _flush_images(self, batch: list[tuple[Path, os.stat_result, Image.Image, int, int, str | None, str | None]]) -> None:
        if not batch:
            return
        self._update(state="indexing", phase="embedding")
        try:
            vectors = self.embedder.embed_images([b[2] for b in batch])
        except Exception as exc:
            for path, st, *_ in batch:
                self._record_error(path, st, f"embed: {type(exc).__name__}: {exc}")
            self._bump(done=len(batch), errors=len(batch))
            return
        for (path, st, image, width, height, taken, text), vector in zip(batch, vectors):
            try:
                media_id = db.upsert_media(
                    path=str(path),
                    kind="image",
                    size=st.st_size,
                    mtime=st.st_mtime,
                    width=width,
                    height=height,
                    duration=None,
                    taken_at=taken,
                )
                db.set_ocr_text(media_id, text)
                old = db.delete_items_for_media(media_id)
                if old:
                    self._ensure_index().remove(old)
                thumb = _save_thumb(image, f"{path}@{st.st_mtime_ns}")
                item_ids = db.add_items(
                    media_id,
                    [{"t": None, "thumb": thumb, "vector": vector.tobytes()}],
                )
                self._ensure_index().add(item_ids, vector.reshape(1, -1))
                self._dirty = True
                self._bump(done=1, indexed=1, added_items=1)
            except Exception as exc:
                self._record_error(path, st, f"store: {type(exc).__name__}: {exc}")
                self._bump(done=1, errors=1)
            image.close()
        self._update(current=None)

    # -- videos --------------------------------------------------------------

    def _probe(self, path: Path) -> dict[str, Any]:
        cmd = [
            config.FFPROBE, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        out = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=True,
        )
        data = json.loads(out.stdout or "{}")
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        fmt = data.get("format", {})
        duration = fmt.get("duration") or video.get("duration")
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            duration_f = 0.0
        taken = fmt.get("tags", {}).get("creation_time")
        if taken:
            try:
                taken = datetime.fromisoformat(taken.replace("Z", "+00:00")).isoformat(timespec="seconds")
            except ValueError:
                taken = None
        return {
            "duration": duration_f,
            "width": int(video.get("width") or 0) or None,
            "height": int(video.get("height") or 0) or None,
            "taken_at": taken,
        }

    def _index_video(self, path: Path, st: os.stat_result) -> None:
        self._update(state="indexing", phase="video", current=path.name)
        workdir = Path(tempfile.mkdtemp(prefix="pf_", dir=str(config.DATA_DIR)))
        try:
            info = self._probe(path)
            duration = info["duration"]
            interval = config.VIDEO_INTERVAL
            if duration and duration / interval > config.VIDEO_MAX_FRAMES:
                interval = duration / config.VIDEO_MAX_FRAMES
            cmd = [
                config.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(path),
                "-vf", f"fps=1/{interval:.4f},scale=min({config.EXTRACT_WIDTH}\\,iw):-2",
                "-q:v", "3", "-start_number", "0", str(workdir / "f_%05d.jpg"),
            ]
            subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=3600, check=True,
            )
            frames = sorted(workdir.glob("f_*.jpg"))
            if not frames:
                raise RuntimeError("no frames could be decoded")

            pending: list[tuple[Image.Image, float]] = []
            kept: list[tuple[Image.Image, float, Any]] = []
            last_vec = None
            for i, frame_path in enumerate(frames):
                if self._cancel.is_set():
                    break
                t = round(i * interval, 3)
                with Image.open(frame_path) as im:
                    im.thumbnail((config.EXTRACT_WIDTH, config.EXTRACT_WIDTH), Image.Resampling.LANCZOS)
                    image = im.convert("RGB")
                pending.append((image, t))
                if len(pending) >= config.EMBED_BATCH:
                    kept.extend(self._dedupe(self.embedder.embed_images([p[0] for p in pending]), pending, last_vec))
                    last_vec = kept[-1][2] if kept else last_vec
                    pending = []
            if pending:
                kept.extend(self._dedupe(self.embedder.embed_images([p[0] for p in pending]), pending, last_vec))

            if not kept:
                raise RuntimeError("no frames survived sampling")

            media_id = db.upsert_media(
                path=str(path), kind="video", size=st.st_size, mtime=st.st_mtime,
                width=info["width"], height=info["height"], duration=duration or None,
                taken_at=info["taken_at"],
            )
            old = db.delete_items_for_media(media_id)
            if old:
                self._ensure_index().remove(old)

            rows = []
            vectors = []
            for image, t, vector in kept:
                rows.append(
                    {
                        "t": t,
                        "thumb": _save_thumb(image, f"{path}@{t}@{st.st_mtime_ns}"),
                        "vector": vector.tobytes(),
                    }
                )
                vectors.append(vector)
                image.close()
            item_ids = db.add_items(media_id, rows)
            import numpy as np

            self._ensure_index().add(item_ids, np.stack(vectors))
            self._dirty = True
            self._bump(done=1, indexed=1, added_items=len(item_ids))
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()[-1:] or [str(exc)]
            self._record_error(path, st, f"ffmpeg: {detail[0]}")
            self._bump(done=1, errors=1)
        except Exception as exc:
            self._record_error(path, st, f"video: {type(exc).__name__}: {exc}")
            self._bump(done=1, errors=1)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            for image, _t in locals().get("pending", []):
                image.close()
            self._update(current=None)

    @staticmethod
    def _dedupe(vectors: Any, pending: list[tuple[Image.Image, float]], last_vec: Any) -> list[tuple[Image.Image, float, Any]]:
        kept: list[tuple[Image.Image, float, Any]] = []
        import numpy as np

        for vector, (image, t) in zip(vectors, pending):
            if last_vec is not None and float(np.dot(last_vec, vector)) >= config.VIDEO_DEDUPE:
                image.close()
                continue
            kept.append((image, t, vector))
            last_vec = vector
        return kept

    def _cleanup_frames(self) -> None:
        # Frames live in the per-video temp dir which is removed by the caller.
        pass

    # -- helpers -------------------------------------------------------------

    def _is_fresh(self, path: str, st: os.stat_result) -> bool:
        if self._force:
            return False
        row = db.media_by_path(path)
        if row is None:
            return False
        # When OCR becomes available after a library was indexed, images are
        # reprocessed once to pick up their text (NULL means "not OCR'd yet").
        if row["kind"] == "image" and row["ocr_text"] is None and ocr.available():
            return False
        return (
            row["status"] == "ready"
            and int(row["size"]) == st.st_size
            and abs(float(row["mtime"]) - st.st_mtime) < 0.5
        )

    def _record_error(self, path: Path, st: os.stat_result, message: str) -> None:
        try:
            db.upsert_media(
                path=str(path), kind="image" if path.suffix.lower() in config.IMAGE_EXTS else "video",
                size=st.st_size, mtime=st.st_mtime, width=None, height=None,
                duration=None, taken_at=None, status="error", error=message,
            )
        except Exception:
            pass

    def _remove_missing(self, root: str, seen: set[str]) -> int:
        removed_media = 0
        for path in db.media_under(root):
            if path in seen:
                continue
            row = db.media_by_path(path)
            if row is None:
                continue
            thumbs = db.thumbs_for_media(int(row["id"]))
            ids = db.delete_media(int(row["id"]))
            if ids:
                self._ensure_index().remove(ids)
                self._dirty = True
            db.delete_thumb_files(thumbs)
            removed_media += 1
        return removed_media

    def _save_index(self, force: bool = False) -> None:
        if not self.index:
            return
        if self._dirty or force:
            self.index.save()
            self._dirty = False


def index_paths(paths: list[str]) -> None:
    """Convenience for the CLI: index paths and wait until the queue is empty."""
    manager = IngestManager()
    manager.start()
    for path in paths:
        manager.enqueue(path)
    while True:
        status = manager.status()
        if status["state"] in ("done", "error", "cancelled") and status["queued"] == 0:
            break
        time.sleep(0.25)
    manager.shutdown()
