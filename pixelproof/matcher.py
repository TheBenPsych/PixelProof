"""Compares outside images against your library and sorts each hit into a category."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config
from .fingerprint import hash_distance, open_image, phash_pair, same_photo_score

CATEGORY_LABELS = {"exact": "Exact copy", "edited": "Edited copy", "possible": "Possible"}
CATEGORY_ORDER = {"exact": 0, "edited": 1, "possible": 2}


@dataclass
class Candidate:
    image_id: int
    clip_sim: float
    hash_dist: int
    mirrored: bool
    category: str
    note: str = ""
    row: object = None
    match_id: str = ""


@dataclass
class Query:
    """One outside image (from a web page or a file) and what it matched."""
    source: str                 # image URL or file path
    width: int = 0
    height: int = 0
    thumb: bytes = b""
    error: str = ""
    matches: list[Candidate] = field(default_factory=list)


def categorize(hash_dist: int, clip_sim: float, mirrored: bool) -> tuple[str | None, str]:
    if hash_dist <= config.EXACT_MAX_HASH_DIST:
        if mirrored:
            return "edited", "flipped left-to-right"
        return "exact", "same picture (maybe resized or recompressed)"
    if clip_sim >= config.EDITED_MIN_CLIP:
        return "edited", "looks edited (crop, filter, text or watermark)"
    if clip_sim >= config.POSSIBLE_MIN_CLIP:
        return "possible", "similar, needs a human look"
    return None, ""


def match_images(images, store, clip, k: int = 5) -> list[list[Candidate]]:
    """images: list of small RGB PIL images. Returns matches for each, best first."""
    if not images:
        return []
    vecs = clip.embed(images)
    pairs = [phash_pair(im) for im in images]
    sims, clip_ids = store.search_clip(vecs, k)
    _, hash_ids_n = store.search_hash([p[0] for p in pairs], k)
    _, hash_ids_m = store.search_hash([p[1] for p in pairs], k)

    results, ours = [], {}
    for qi in range(len(images)):
        ids = {int(i) for i in np.concatenate([clip_ids[qi], hash_ids_n[qi], hash_ids_m[qi]]) if i >= 0}
        rows = store.get_images(ids)
        found = []
        for image_id, row in rows.items():
            lib_vec = np.frombuffer(row["clip"], dtype=np.float32)
            sim = float(np.dot(lib_vec, vecs[qi]))
            d_normal = hash_distance(row["phash"], pairs[qi][0])
            d_mirror = hash_distance(row["phash"], pairs[qi][1])
            mirrored = d_mirror < d_normal
            dist = min(d_normal, d_mirror)
            cat, note = categorize(dist, sim, mirrored)
            if cat and dist > config.EXACT_MAX_HASH_DIST:   # the picture hash did not already prove it
                cat, note = double_check(images[qi], row, mirrored, cat, note, ours)
            if cat:
                found.append(Candidate(image_id, sim, dist, mirrored, cat, note, row))
        found.sort(key=lambda c: (CATEGORY_ORDER[c.category], c.hash_dist, -c.clip_sim))
        results.append(found)
    return results


def double_check(theirs, row, mirrored, cat, note, cache):
    """The AI says the two pictures look alike. Make sure it is the same PHOTO, not just the same person or
    scene: their small details must line up. Returns (category, note), or (None, "") to drop the hit."""
    key = row["id"]
    if key not in cache:
        try:
            cache[key] = open_image(row["path"])[0]
        except Exception:
            cache[key] = None
    if cache[key] is None:
        return cat, note + " (our file was not found, so it could not be double-checked)"
    points = same_photo_score(theirs, cache[key], mirrored)
    if points is None:
        return cat, note
    if points < config.SAME_PHOTO_MIN_POINTS:
        return None, ""
    if cat == "possible":
        cat, note = "edited", "same photo, edited"
    return cat, f"{note}; {points} details line up" + ("; flipped left-to-right" if mirrored and "flipped" not in note else "")
