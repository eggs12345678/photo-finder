"""Optional local OCR: recover on-screen text so screenshots can be found by
what they say, not only by what they look like.

Engines are optional and tried in order of convenience:

1. ``windows`` - the OCR engine built into Windows 10/11 (winsdk package and a
   language pack, no model download, tens of milliseconds per image).
2. ``rapidocr`` - pip-installable ONNX OCR (`rapidocr_onnxruntime`).
3. ``tesseract`` - `pytesseract` plus a local Tesseract binary.

Set ``PHOTOFINDER_OCR=auto`` (default) to use whichever is importable, or name
an engine, or ``off``. Nothing is sent anywhere.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

from PIL import Image, ImageOps

from . import config

# Unicode words and numbers; keeps non-Latin scripts usable.
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

_engine = None
_engine_error: str | None = None
_last_error: str | None = None
_detected = False
_lock = threading.Lock()


def tokens(text: str) -> list[str]:
    """Unique lowercase tokens in first-seen order, capped for query safety."""
    seen: list[str] = []
    for token in TOKEN_RE.findall(text.lower()):
        if token not in seen:
            seen.append(token)
        if len(seen) >= config.OCR_MAX_TOKENS:
            break
    return seen


def normalize(text: str) -> str:
    return " ".join(text.split())[: config.OCR_MAX_CHARS]


class WindowsOcr:
    name = "windows"

    def __init__(self) -> None:
        from winsdk.windows.globalization import Language
        from winsdk.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_language(Language(config.OCR_LANGUAGE))
        if engine is None:
            raise RuntimeError(f"Windows OCR has no {config.OCR_LANGUAGE} language pack")
        self._ocr = engine
        self._max_side = min(config.OCR_MAX_SIDE, int(OcrEngine.max_image_dimension))

    def extract(self, path: Path) -> str:
        import asyncio

        return asyncio.run(self._extract(path))

    async def _extract(self, path: Path) -> str:
        from winsdk.windows.graphics.imaging import (
            BitmapAlphaMode,
            BitmapPixelFormat,
            SoftwareBitmap,
        )
        from winsdk.windows.storage.streams import DataWriter

        with Image.open(path) as im:
            image = ImageOps.exif_transpose(im).convert("RGB")
        if max(image.size) > self._max_side:
            scale = self._max_side / max(image.size)
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        writer = DataWriter()
        writer.write_bytes(image.convert("RGBA").tobytes("raw", "BGRA"))
        buffer = writer.detach_buffer()
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            buffer,
            BitmapPixelFormat.BGRA8,
            image.width,
            image.height,
            BitmapAlphaMode.PREMULTIPLIED,
        )
        result = await self._ocr.recognize_async(bitmap)
        return " ".join(line.text for line in result.lines)


class RapidOcr:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def extract(self, path: Path) -> str:
        import numpy as np

        with Image.open(path) as im:
            image = ImageOps.exif_transpose(im).convert("RGB")
        if max(image.size) > config.OCR_MAX_SIDE:
            scale = config.OCR_MAX_SIDE / max(image.size)
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        result, _ = self._ocr(np.array(image))
        return " ".join(line[1] for line in (result or []))


class TesseractOcr:
    name = "tesseract"

    def __init__(self) -> None:
        import pytesseract

        pytesseract.get_tesseract_version()  # raises when the binary is missing
        self._ocr = pytesseract

    def extract(self, path: Path) -> str:
        with Image.open(path) as im:
            image = ImageOps.exif_transpose(im).convert("RGB")
        language = config.OCR_LANGUAGE.split("-")[0]
        return str(self._ocr.image_to_string(image, lang=language))


def _build():
    global _engine, _engine_error
    mode = config.OCR_MODE
    if mode in {"off", "0", "false", "no", "none"}:
        return None
    order = (
        ["windows", "rapidocr", "tesseract"]
        if mode in {"auto", "on", "1", "true", "yes"}
        else [mode]
    )
    errors: list[str] = []
    for name in order:
        try:
            if name == "windows":
                if sys.platform != "win32":
                    raise RuntimeError("Windows only")
                engine = WindowsOcr()
            elif name == "rapidocr":
                engine = RapidOcr()
            elif name == "tesseract":
                engine = TesseractOcr()
            else:
                raise RuntimeError(f"unknown OCR engine {name!r}")
            _engine = engine
            _engine_error = None
            return engine
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    _engine = None
    _engine_error = "; ".join(errors) or "no OCR engine available"
    return None


def _get():
    global _detected
    with _lock:
        if not _detected:
            _detected = True
            _build()
        return _engine


def available() -> bool:
    return _get() is not None


def engine_name() -> str | None:
    engine = _get()
    return engine.name if engine else None


def status() -> dict[str, str | None]:
    engine = _get()
    return {
        "engine": engine.name if engine else None,
        "error": None if engine else _engine_error,
        "last_error": _last_error,
    }


def extract(path: str | Path) -> str | None:
    """Text visible in an image, or None when OCR is unavailable.

    An empty string means the image was processed and had no readable text,
    which lets the scanner tell "not done yet" apart from "nothing there".
    """
    global _last_error
    engine = _get()
    if engine is None:
        return None
    try:
        return normalize(engine.extract(Path(path)))
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        return ""
