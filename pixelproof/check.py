"""Check one page (or one picture) against your library and make a report."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import config
from .fingerprint import open_image, thumbnail_jpeg
from .matcher import Query, match_images
from .page import (domain_of, download_images, extract_image_urls, fetch_page, is_banned,
                   load_banned, session)
from .store import Store, match_id, now


@dataclass
class CheckResult:
    target: str
    page_url: str = ""
    domain: str = ""
    checked_at: str = ""
    banned: bool = False
    error: str = ""
    queries: list[Query] = field(default_factory=list)
    skipped_small: int = 0
    failed: int = 0

    @property
    def matched(self) -> list[Query]:
        return [q for q in self.queries if q.matches]


def _is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def check_target(target: str, store: Store, clip, data_dir, log=print) -> CheckResult:
    target = target.strip().strip('"')
    res = CheckResult(target=target, checked_at=now())
    sources: list[tuple[str, bytes | str | None, str]] = []  # (label, bytes or path, error)

    if _is_url(target):
        res.page_url, res.domain = target, domain_of(target)
        if is_banned(target, load_banned(os.path.join(data_dir, "banned.txt"))):
            res.banned = True
            log(f"{res.domain} is on your ban list, so it was not checked.")
            return res
        sess = session()
        try:
            final_url, html, direct = fetch_page(target, sess)
        except Exception as e:
            res.error = f"Could not open the page: {type(e).__name__}: {e}"
            log(res.error)
            return res
        res.page_url, res.domain = final_url, domain_of(final_url)
        if direct is not None:
            sources.append((final_url, direct, ""))
        else:
            urls = extract_image_urls(html, final_url)
            log(f"Found {len(urls)} pictures on the page. Downloading ...")
            sources.extend(download_images(urls, final_url, sess))
    elif os.path.isfile(target):
        res.page_url, res.domain = "", "file on this PC"
        sources.append((target, target, ""))
    else:
        res.error = "That is not a web link or a picture file on this PC."
        log(res.error)
        return res

    images, queries = [], []
    for label, data, err in sources:
        q = Query(source=label)
        if err or data is None:
            q.error = err or "download failed"
            res.failed += 1
            continue
        try:
            img, (w, h) = open_image(data)
        except Exception as e:
            q.error = f"not a readable picture ({type(e).__name__})"
            res.failed += 1
            continue
        q.width, q.height = w, h
        if min(w, h) < config.MIN_WEB_IMAGE_SIDE:
            res.skipped_small += 1
            continue
        q.thumb = thumbnail_jpeg(img)
        images.append(img)
        queries.append(q)

    log(f"Comparing {len(images)} pictures with your library ...")
    for start in range(0, len(images), 32):
        for q, matches in zip(queries[start:start + 32], match_images(images[start:start + 32], store, clip)):
            q.matches = matches
    res.queries = queries

    # Save every match so it has a tracking ID (used later by Proof of Discovery).
    for q in res.matched:
        for c in q.matches:
            fid, found_at = store.save_finding(res.page_url or "", q.source, res.domain, c.image_id,
                                               c.category, c.clip_sim, c.hash_dist, c.note)
            c.match_id = match_id(fid, found_at)
    store.commit()
    return res
