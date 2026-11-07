"""Prepares downloaded pictures on all the processor's cores at once.

Python can only run one piece of picture work at a time inside one program, even with many sites running
side by side. So the heavy part (reading the picture, shrinking it for the AI, and making its picture hash)
is handed to helper programs, one per core. Each returns only small results: the picture's size, the
224x224 pixels the AI looks at, and the two picture hashes. Reading a page's HTML to find its pictures is
also done by the helpers, as that is slow on big news pages.

If the helpers can't start for any reason, everything still works the old way, just slower.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

AI_SIZE = 224      # the AI looks at a 224 x 224 square
_pool = None
_pool_lock = threading.Lock()
_broken = False


@dataclass
class Prepared:
    width: int
    height: int
    pixels: np.ndarray        # AI_SIZE x AI_SIZE x 3, uint8
    hash_normal: bytes
    hash_mirror: bytes


def ai_pixels(img: Image.Image, size: int = AI_SIZE) -> np.ndarray:
    """Exactly what the AI's own preparation does before its number steps: shrink the short side to `size`
    (bicubic) and cut the middle square."""
    w, h = img.size
    if w <= h:
        nw, nh = size, int(size * h / w)
    else:
        nw, nh = int(size * w / h), size
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.Resampling.BICUBIC)
    left = int(round((nw - size) / 2.0))
    top = int(round((nh - size) / 2.0))
    return np.asarray(img.crop((left, top, left + size, top + size)).convert("RGB"), dtype=np.uint8)


def prepare(data: bytes, min_side: int = 0):
    """Runs inside a helper. Returns Prepared, "small" for a picture under min_side, or an error message."""
    from .fingerprint import open_image, phash_pair

    try:
        img, (w, h) = open_image(data)
    except Exception as e:
        return f"not a readable picture ({type(e).__name__})"
    if min(w, h) < min_side:
        return "small"
    hn, hm = phash_pair(img)
    return Prepared(w, h, ai_pixels(img), hn, hm)


def same_photo_files(their_data: bytes, our_path: str, mirrored: bool):
    """Runs inside a helper: the double-check (see matcher.double_check), opening both pictures here.
    Returns the number of details that line up, None if the checker isn't installed, or "missing"."""
    from .fingerprint import open_image, same_photo_score

    try:
        ours = open_image(our_path)[0]
    except Exception:
        return "missing"
    try:
        theirs = open_image(their_data)[0]
    except Exception:
        return 0
    return same_photo_score(theirs, ours, mirrored)


def helpers() -> int:
    return max(2, min(16, (os.cpu_count() or 4) - 2))


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            # "spawn" is how Windows starts helpers anyway; using it everywhere keeps tests honest
            _pool = ProcessPoolExecutor(max_workers=helpers(), mp_context=multiprocessing.get_context("spawn"))
        return _pool


def prepare_many(datas: list[bytes], min_side: int = 0, use_helpers: bool = True) -> list:
    """Prepares many pictures, using the helpers when possible. Same order as `datas`."""
    global _broken
    if use_helpers and not _broken and len(datas) > 0:
        try:
            futs = [_get_pool().submit(prepare, d, min_side) for d in datas]
            return [f.result() for f in futs]   # prepare() never raises, so any error here is the helpers'
        except Exception:   # helpers could not start or crashed: fall back for the rest of this run
            _broken = True
    return [prepare(d, min_side) for d in datas]


def run(fn, *args):
    """Runs fn(*args) in a helper (fn must be a plain module-level function). Falls back to running it here."""
    global _broken
    if not _broken:
        from concurrent.futures.process import BrokenProcessPool

        try:
            fut = _get_pool().submit(fn, *args)
        except Exception:      # helpers could not start
            _broken = True
            return fn(*args)
        try:
            return fut.result()
        except BrokenProcessPool:
            _broken = True     # a helper crashed: do the rest here
    return fn(*args)


def shutdown():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
