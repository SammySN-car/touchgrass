"""CLIP vision scoring: embeds quest text + photos, returns similarity.

Runs on CPU only so gemma3:4b keeps the GPU. Loaded once into a module-level
singleton; every failure path returns None so callers can skip the gate.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from PIL import Image
import io

log = logging.getLogger(__name__)

MODEL_ID = "clip-vit-base-patch32"
_Scorer = Callable[[str, list[bytes]], list[float]]

_SCORER_CACHE: dict = {}


def _load_scorer_impl():
    """Load CLIP once. Returns an object with .score(quest_text, images)->list[float]."""
    from transformers import CLIPModel, CLIPProcessor

    model = CLIPModel.from_pretrained(MODEL_ID)
    processor = CLIPProcessor.from_pretrained(MODEL_ID)
    model.eval()

    class ClipScorer:
        def score(self, quest_text: str, images: list[bytes]) -> list[float]:
            import torch

            pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
            inputs = processor(
                text=[quest_text], images=pil, return_tensors="pt", padding=True
            )
            with torch.no_grad():
                out = model(**inputs)
            img = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
            txt = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
            sims = (img @ txt.T).squeeze(-1)
            return [float(s) for s in sims.tolist()]

    return ClipScorer()


def get_scorer():
    """Return the singleton scorer, or None if it cannot be loaded."""
    if "scorer" in _SCORER_CACHE:
        return _SCORER_CACHE["scorer"]
    try:
        scorer = _load_scorer_impl()
    except Exception as exc:  # noqa: BLE001 - any load failure skips the gate
        log.warning("vision scorer unavailable: %s", exc)
        return None
    _SCORER_CACHE["scorer"] = scorer
    return scorer
