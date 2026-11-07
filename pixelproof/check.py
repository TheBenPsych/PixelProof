"""Check one page (or one picture) against your library and make a report."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

from . import config
from .fingerprint import open_image, thumbnail_jpeg
from .matcher import Query, match_images
from .page import (domain_of, download_images, extract_image_urls, fetch_page, is_banned,
                   load_banned, session)
from .sites import load_approvals, load_sisters, path_key
from .store import Store, match_id, now


@dataclass
class Group:
    """One of our images and every copy of it on the page (often the same picture in several sizes)."""
    image_id: int
    row: object
    best: object                       # the strongest Candidate
    copies: list = field(default_factory=list)  # (Query, Candidate), largest copy first
    match_id: str = ""
    finding_id: int = 0
    found_at: str = ""                                # first time this page showed this photo
    licensed: str | None = None                       # site or company that makes it licensed
    elsewhere: list = field(default_factory=list)     # other pages where this photo was found before

    @property
    def main(self):
        return self.copies[0]


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
    groups: list[Group] = field(default_factory=list)
    sister_group: object = None                        # SisterGroup this site belongs to, if any
    sister_pages: list = field(default_factory=list)   # same link ending on other sites

    @property
    def stolen(self) -> list[Group]:
        return [g for g in self.groups if not g.licensed]

    @property
    def licensed(self) -> list[Group]:
        return [g for g in self.groups if g.licensed]

    @property
    def matched(self) -> list[Query]:
        return [q for q in self.queries if q.matches]


def group_matches(queries: list[Query]) -> list[Group]:
    from .matcher import CATEGORY_ORDER

    def strength(c):
        return (CATEGORY_ORDER[c.category], c.hash_dist, -c.clip_sim)

    groups: dict[int, Group] = {}
    for q in queries:
        for c in q.matches:
            g = groups.get(c.image_id)
            if g is None:
                g = groups[c.image_id] = Group(c.image_id, c.row, c)
            elif strength(c) < strength(g.best):
                g.best = c
            g.copies.append((q, c))
    for g in groups.values():
        g.copies.sort(key=lambda qc: (strength(qc[1])[0], -(qc[0].width * qc[0].height)))
    return sorted(groups.values(), key=lambda g: strength(g.best))


def _is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def check_target(target: str, store: Store, clip, data_dir, log=print) -> CheckResult:
    target = target.strip().strip('"')
    res = CheckResult(target=target, checked_at=now())
    sources: list[tuple[str, bytes | str | None, str]] = []  # (label, bytes or path, error)
    page_html: bytes | None = None

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
            page_html = html.encode("utf-8", "replace")
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
    raw: dict[str, bytes | str] = {}   # picture as downloaded (kept as evidence for matches)
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
        raw[label] = data
        images.append(img)
        queries.append(q)

    log(f"Comparing {len(images)} pictures with your library ...")
    for start in range(0, len(images), 32):
        for q, matches in zip(queries[start:start + 32], match_images(images[start:start + 32], store, clip)):
            q.matches = matches
    res.queries = queries

    res.groups = group_matches(queries)

    # Save one finding per image of ours, so it has a tracking ID (used later by Proof of Discovery).
    key = path_key(res.page_url)
    for g in res.groups:
        q, _ = g.main
        others = [cq.source for cq, _ in g.copies[1:]]
        note = g.best.note + (f"; also at {len(others)} other image links on the page" if others else "")
        fid, found_at = store.save_finding(res.page_url or "", q.source, res.domain, g.image_id,
                                           g.best.category, g.best.clip_sim, g.best.hash_dist, note,
                                           path_key=key, thumb=q.thumb or None, width=q.width, height=q.height)
        g.match_id, g.found_at, g.finding_id = match_id(fid, found_at), found_at, fid
        try:
            save_evidence(store, fid, g.match_id, data_dir, res, q, raw.get(q.source), page_html)
        except OSError as e:  # evidence is a bonus, never stop the check for it
            log(f"Could not save the evidence copy for {g.match_id}: {e}")
        for _, c in g.copies:
            c.match_id = g.match_id
        g.elsewhere = store.other_findings(g.image_id, res.page_url or q.source)
    if res.page_url:
        store.save_page(res.page_url, res.domain, key, len(queries), len(res.groups))
        if key:
            res.sister_pages = [p for p in store.pages_with_key(key, res.page_url) if p["domain"] != res.domain]
    store.commit()

    # Sister sites and licensed use (read fresh each time, so edits to the .txt files count right away).
    sisters = load_sisters(data_dir, store)
    approvals = load_approvals(data_dir, sisters)
    res.sister_group = sisters.group_of(res.domain) if res.page_url else None
    for g in res.groups:
        g.licensed = approvals.licensed(g.row["photographer"], res.domain) if res.page_url else None
    return res


def _ext_for(name: str, data: bytes) -> str:
    sig = {b"\xff\xd8": ".jpg", b"\x89PNG": ".png", b"GIF8": ".gif", b"RIFF": ".webp", b"BM": ".bmp"}
    for magic, ext in sig.items():
        if data.startswith(magic):
            return ext
    m = re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff?|avif|heic)(?:$|[?#])", name, re.I)
    return "." + m.group(1).lower() if m else ".img"


def save_evidence(store: Store, finding_id: int, mid: str, data_dir, res: CheckResult, q, data, page_html):
    """The first time a match is found, keep their picture (exactly as downloaded) and the page,
    with SHA-256 fingerprints, in data\\evidence\\<match ID>. Later checks never overwrite it."""
    row = store.db.execute("SELECT evidence_dir FROM findings WHERE id=?", (finding_id,)).fetchone()
    if row is None or row[0]:
        return
    if isinstance(data, str):            # a picture file on this PC
        with open(data, "rb") as f:
            data = f.read()
    if not data:
        return
    folder = os.path.join(data_dir, "evidence", mid)
    os.makedirs(folder, exist_ok=True)
    their_file = os.path.join(folder, "theirs" + _ext_for(q.source, data))
    with open(their_file, "wb") as f:
        f.write(data)
    their_sha = hashlib.sha256(data).hexdigest()
    page_sha = None
    if page_html:
        with open(os.path.join(folder, "page.html"), "wb") as f:
            f.write(page_html)
        page_sha = hashlib.sha256(page_html).hexdigest()
    lines = [f"Match ID: {mid}", f"Saved: {res.checked_at}", f"Page: {res.page_url or '(file on this PC)'}",
             f"Picture: {q.source}", f"Picture SHA-256: {their_sha}"]
    if page_sha:
        lines.append(f"Page SHA-256: {page_sha}")
    with open(os.path.join(folder, "info.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    store.save_evidence(finding_id, folder, their_sha, page_sha)
