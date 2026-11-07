"""Compares outside images against your library and sorts each hit into a category."""

from __future__ import annotations

import time
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
    return _match(vecs, pairs, lambda qi: images[qi], store, k)


def match_prepared(preps, store, clip, raw_of, k: int = 5, stats: dict | None = None) -> list[list[Candidate]]:
    """Like match_images, for pictures the helpers prepared (see prep.py). raw_of(i) gives picture i as
    downloaded; the rare double-check then also runs in a helper. stats (optional) collects timings."""
    if not preps:
        return []
    t0 = time.time()
    vecs = clip.embed_pixels([p.pixels for p in preps])
    if stats is not None:
        stats["ai"] = stats.get("ai", 0.0) + time.time() - t0
    pairs = [(p.hash_normal, p.hash_mirror) for p in preps]
    return _match(vecs, pairs, None, store, k, raw_of=raw_of, stats=stats)


def _match(vecs, pairs, image_of, store, k, raw_of=None, stats=None):
    t0 = time.time()
    double0 = stats.get("double", 0.0) if stats is not None else 0.0
    sims, clip_ids = store.search_clip(vecs, k)
    hd_n, hash_ids_n = store.search_hash([p[0] for p in pairs], k)
    hd_m, hash_ids_m = store.search_hash([p[1] for p in pairs], k)

    results, ours = [], {}
    near = config.POSSIBLE_MIN_CLIP - 0.02   # small margin: the library search is a little rounded on the card
    for qi in range(len(pairs)):
        # only look up library pictures that could be a hit (most web pictures have none, saving the lookup)
        ids = {int(i) for i, s in zip(clip_ids[qi], sims[qi]) if i >= 0 and s >= near}
        ids |= {int(i) for i, d in zip(np.concatenate([hash_ids_n[qi], hash_ids_m[qi]]),
                                       np.concatenate([hd_n[qi], hd_m[qi]])) if i >= 0 and d <= config.EXACT_MAX_HASH_DIST}
        if not ids:
            results.append([])
            continue
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
            # double-check unless the picture hash proved it AND the AI agrees it looks the same
            if cat and (dist > config.EXACT_MAX_HASH_DIST or sim < config.HASH_HIT_MIN_CLIP):
                t1 = time.time()
                if raw_of is not None:
                    cat, note = double_check_in_helper(raw_of(qi), row, mirrored, cat, note)
                else:
                    cat, note = double_check(image_of(qi), row, mirrored, cat, note, ours)
                if stats is not None:
                    stats["double"] = stats.get("double", 0.0) + time.time() - t1
                    stats["n_double"] = stats.get("n_double", 0) + 1
            if cat:
                found.append(Candidate(image_id, sim, dist, mirrored, cat, note, row))
        found.sort(key=lambda c: (CATEGORY_ORDER[c.category], c.hash_dist, -c.clip_sim))
        results.append(found)
    if stats is not None:
        stats["search"] = stats.get("search", 0.0) + time.time() - t0 - stats.get("double", 0.0) + double0
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
        return _decide("missing", mirrored, cat, note)
    return _decide(same_photo_score(theirs, cache[key], mirrored), mirrored, cat, note)


def double_check_in_helper(their_data, row, mirrored, cat, note):
    """Same as double_check, run in a helper program (see prep.py) so it uses another processor core."""
    from .prep import run, same_photo_files

    return _decide(run(same_photo_files, their_data, row["path"], mirrored), mirrored, cat, note)


def _decide(points, mirrored, cat, note):
    if points == "missing":
        if cat == "possible":   # only "looks alike" and nothing to double-check against: not shown
            return None, ""
        return cat, note + " (our file was not found, so it could not be double-checked)"
    if points is None:
        return cat, note
    if points < config.SAME_PHOTO_MIN_POINTS:
        return None, ""
    if cat == "possible":
        cat, note = "edited", "same photo, edited"
    return cat, f"{note}; {points} details line up" + ("; flipped left-to-right" if mirrored and "flipped" not in note else "")
