"""Configuration for photo-finder.

Everything is local. Data (SQLite, FAISS index, thumbnails) lives in
``server/data`` by default and never leaves the machine.
"""

from __future__ import annotations

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = SERVER_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


DATA_DIR = _env_path("PHOTOFINDER_DATA", SERVER_DIR / "data")
DB_PATH = DATA_DIR / "library.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"

# One of open_clip's (model, pretrained) pairs. ViT-B-32 is the speed/quality
# sweet spot; ViT-B-16 is noticeably better at scenes and text but ~4x slower.
MODEL_NAME = os.environ.get("PHOTOFINDER_MODEL", "ViT-B-32")
MODEL_PRETRAINED = os.environ.get("PHOTOFINDER_PRETRAINED", "laion2b_s34b_b79k")

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
    ".heic", ".heif", ".avif",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg",
}

# Video sampling. One frame every VIDEO_INTERVAL seconds, capped per clip and
# deduplicated so static scenes do not flood the index.
VIDEO_INTERVAL = float(os.environ.get("PHOTOFINDER_VIDEO_INTERVAL", "2.0"))
VIDEO_MAX_FRAMES = int(os.environ.get("PHOTOFINDER_VIDEO_MAX_FRAMES", "300"))
VIDEO_DEDUPE = float(os.environ.get("PHOTOFINDER_VIDEO_DEDUPE", "0.982"))

THUMB_SIZE = 512
EMBED_BATCH = int(os.environ.get("PHOTOFINDER_BATCH", "24"))
EXTRACT_WIDTH = 960
FFMPEG = os.environ.get("PHOTOFINDER_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("PHOTOFINDER_FFPROBE", "ffprobe")

# Default number of distinct media returned per search.
DEFAULT_LIMIT = 60


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
