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
        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.input_size = 224
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
            size = getattr(getattr(model, "visual", None), "image_size", 224)
            if isinstance(size, (tuple, list)):
                size = max(int(part) for part in size)
            self.input_size = int(size) or 224
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

        tensors = torch.stack([self._prepare(img) for img in images]).to(self.device)
        with self._infer_lock, torch.inference_mode():
            feats = self._model.encode_image(tensors)
        return self._normalise(feats.detach().cpu().numpy())

    def _prepare(self, image: Image.Image):
        """Preprocess one image, keeping the whole frame for wide screenshots.

        CLIP's default transform resizes the short side and then centre-crops a
        square, which discards the left and right of a 16:9 screenshot. Resizing
        to the model's square input first keeps every pixel (with a mild
        horizontal squeeze), which measured better than cropping or padding.
        """
        image = image.convert("RGB")
        if config.EMBED_SQUASH:
            size = (self.input_size, self.input_size)
            if image.size != size:
                image = image.resize(size, Image.Resampling.BICUBIC)
        return self._preprocess(image)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed several strings in one forward pass -> (n, dim), normalised."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        keys = [text.strip().lower() for text in texts]
        missing = [k for k in keys if k not in self._text_cache]
        if missing:
            self.load()
            import torch

            tokens = self._tokenizer([k for k in missing]).to(self.device)
            with self._infer_lock, torch.inference_mode():
                feats = self._model.encode_text(tokens)
            vectors = self._normalise(feats.detach().cpu().numpy())
            for key, vector in zip(missing, vectors):
                self._text_cache[key] = vector
                self._text_cache.move_to_end(key)
            while len(self._text_cache) > 512:
                self._text_cache.popitem(last=False)
        return np.stack([self._text_cache[k] for k in keys])

    def embed_text(self, text: str) -> np.ndarray:
        """Embed one string -> (dim,) float32, normalised. Cached (LRU)."""
        return self.embed_texts([text])[0]

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a search query as the average of the configured prompts.

        Averaging a few paraphrases is less sensitive to wording than a single
        prompt and shares one tokenizer pass, so it costs about the same.
        """
        query = query.strip()
        key = query.lower()
        cached = self._query_cache.get(key)
        if cached is not None:
            self._query_cache.move_to_end(key)
            return cached
        prompts = [template.format(query=query) for template in config.QUERY_PROMPTS]
        vectors = self.embed_texts(prompts).mean(axis=0)
        norm = float(np.linalg.norm(vectors)) or 1.0
        vector = np.ascontiguousarray(vectors / norm, dtype=np.float32)
        self._query_cache[key] = vector
        self._query_cache.move_to_end(key)
        while len(self._query_cache) > 512:
            self._query_cache.popitem(last=False)
        return vector

    def warmup(self) -> None:
        self.load()
        self.embed_query("warmup")


_shared: ClipEmbedder | None = None
_shared_lock = threading.Lock()


def get_embedder() -> ClipEmbedder:
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = ClipEmbedder()
    return _shared
