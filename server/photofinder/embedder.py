"""CLIP embedding, shared by indexing and search.

The model is loaded once per process. Image and text vectors live in the same
space and are L2-normalised, so a dot product is cosine similarity.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image

from . import config


class ClipEmbedder:
    def __init__(
        self,
        model_name: str = config.MODEL_NAME,
        pretrained: str = config.MODEL_PRETRAINED,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.pretrained = pretrained
        self.device = device or os.environ.get("PHOTOFINDER_DEVICE", "cpu")
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._text_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.dim = 512

    # -- lifecycle -----------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            import open_clip
            import torch

            torch.set_num_threads(max(1, min(8, (os.cpu_count() or 4))))
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained, device=self.device
            )
            model.eval()
            tokenizer = open_clip.get_tokenizer(self.model_name)
            with torch.inference_mode():
                probe = model.encode_text(tokenizer(["warmup"]).to(self.device))
            self.dim = int(probe.shape[-1])
            self._model = model
            self._preprocess = preprocess
            self._tokenizer = tokenizer

    # -- vectors -------------------------------------------------------------

    def _normalise(self, x: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(x, dtype=np.float32)
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        np.maximum(norms, 1e-12, out=norms)
        return x / norms

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        """Embed a batch of RGB PIL images -> (n, dim) float32, normalised."""
        if not images:
            return np.zeros((0, self.dim), dtype=np.float32)
        self.load()
        import torch

        tensors = torch.stack(
            [self._preprocess(img.convert("RGB")) for img in images]
        ).to(self.device)
        with self._infer_lock, torch.inference_mode():
            feats = self._model.encode_image(tensors)
        return self._normalise(feats.detach().cpu().numpy())

    def embed_text(self, text: str) -> np.ndarray:
        """Embed one query -> (dim,) float32, normalised. Cached (LRU)."""
        key = text.strip().lower()
        cached = self._text_cache.get(key)
        if cached is not None:
            self._text_cache.move_to_end(key)
            return cached
        self.load()
        import torch

        tokens = self._tokenizer([text.strip()]).to(self.device)
        with self._infer_lock, torch.inference_mode():
            feats = self._model.encode_text(tokens)
        vec = self._normalise(feats.detach().cpu().numpy())[0]
        self._text_cache[key] = vec
        if len(self._text_cache) > 512:
            self._text_cache.popitem(last=False)
        return vec

    def warmup(self) -> None:
        self.load()
        self.embed_text("warmup")


_shared: ClipEmbedder | None = None
_shared_lock = threading.Lock()


def get_embedder() -> ClipEmbedder:
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = ClipEmbedder()
    return _shared
