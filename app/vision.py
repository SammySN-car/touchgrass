"""CLIP vision scoring: embeds quest text + photos, returns similarity.

Runs on CPU only so gemma3:4b keeps the GPU. Loaded once into a module-level
singleton; load failures return None so callers can skip the gate, while
scoring errors propagate to the caller (the gate layer wraps them).
"""
from __future__ import annotations

import logging
from typing import Callable

from PIL import Image
import io

log = logging.getLogger(__name__)

MODEL_ID = "openai/clip-vit-base-patch32"
_Scorer = Callable[[str, list[bytes]], list[float]]

_SCORER_CACHE: dict = {}
# Permanent failure sentinel for this process: once a load fails, every later
# get_scorer() returns None instantly instead of re-paying from_pretrained's
# network retries on each submission (worst case stays == v1). Tradeoff:
# restart the process to retry a failed load.
_LOAD_FAILED = object()


class _ScorerAdapter:
    """Adapt a ``.score``-only scorer to the gate's callable contract.

    ``ClipScorer`` defines ``__call__`` itself; this covers any other load
    shape so ``get_scorer()`` can never return a non-callable — the gate's
    blanket except would swallow the resulting TypeError and silently accept
    every photo while the model still loads. ``.score`` is kept so existing
    callers/tests using that name keep working.
    """

    def __init__(self, scorer):
        self._scorer = scorer

    def __call__(self, quest_text: str, images: list[bytes]) -> list[float]:
        return self._scorer.score(quest_text, images)

    def score(self, quest_text: str, images: list[bytes]) -> list[float]:
        return self._scorer.score(quest_text, images)


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
            return [max(0.0, min(1.0, float(s))) for s in sims.tolist()]

        def __call__(self, quest_text: str, images: list[bytes]) -> list[float]:
            # Gate contract: scorer(quest_text, images) -> list[float].
            return self.score(quest_text, images)

    return ClipScorer()


def get_scorer() -> _Scorer | None:
    """Return the singleton scorer, or None if it cannot be loaded.

    The return value always satisfies the gate's callable contract
    (``scorer(quest_text, images) -> list[float]``); ``.score`` also remains
    available. Load failures are memoized for the process lifetime (see
    ``_LOAD_FAILED``) so an offline Hub cannot re-stall every submission.
    """
    if "scorer" in _SCORER_CACHE:
        cached = _SCORER_CACHE["scorer"]
        return None if cached is _LOAD_FAILED else cached
    try:
        scorer = _load_scorer_impl()
        if not callable(scorer):
            scorer = _ScorerAdapter(scorer)
    except Exception as exc:  # noqa: BLE001 - any load failure skips the gate
        log.warning("vision scorer unavailable: %s", exc)
        _SCORER_CACHE["scorer"] = _LOAD_FAILED
        return None
    _SCORER_CACHE["scorer"] = scorer
    return scorer
