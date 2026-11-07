"""The summary report: everything PixelProof has found so far, grouped three ways.

  1. Repeat copies     the same photo of ours found on many pages
  2. Sister sites      sites that look like one company (same link endings, or sister-sites.txt)
  3. Photographer/site who was copied, and where

Licensed uses (approved.txt) are kept apart so they don't look like theft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .report import CSS, _b64, _e, _our_thumb, _when
from .sites import load_approvals, load_sisters
from .store import match_id

MAX_REPEAT_CARDS = 150
MAX_THEIR_THUMBS = 8
MAX_MATCH_ROWS = 300

SUMMARY_CSS = """
.wrap{max-width:1200px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;background:var(--bg)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto}
.rep{display:grid;grid-template-columns:240px 1fr;gap:0}
@media (max-width:700px){.rep{grid-template-columns:1fr}}
.rep .ours .imgbox{height:200px} .rep .ours .imgbox img{max-height:200px}
.site{margin:0 0 12px} .site h3{font-size:15px;margin:0 0 4px}
.site ul{margin:0;padding-left:18px} .site li{word-break:break-all;font-size:14px}
.minis{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.minis img{height:64px;border-radius:6px;outline:2px solid var(--theirs-soft)}
.count{font-size:22px;font-weight:700;color:var(--theirs)}
.grp .body{padding:14px 18px}
td.pv img{height:72px;max-width:120px;object-fit:contain;border-radius:6px;display:block}
td a{word-break:break-all}
"""


@dataclass
class Find:
    id: int
    match_id: str
    found_at: str
    last_seen: str
    page: str            # page link (or file path for local checks)
    image_url: str
    domain: str
    company: str         # sister group name, or the site itself
    image_id: int
    our_path: str
    photographer: str
    year: object
    quarter: str
    category: str
    thumb: bytes | None
    licensed: str | None


@dataclass
class Photo:
    image_id: int
    our_path: str
    photographer: str
    year: object
    quarter: str
    finds: list = field(default_factory=list)

    @property
    def pages(self):
        return {f.page for f in self.finds}

    @property
    def sites(self):
        return {f.domain for f in self.finds}


def collect(store, data_dir):
    sisters = load_sisters(data_dir, store)
    approvals = load_approvals(data_dir, sisters)
    seen: dict[tuple, Find] = {}
    for r in store.all_findings():
        page = r["page_url"] or r["image_url"]
        key = (page, r["image_id"])
        if key in seen:  # step 1 saved a new row on every re-check; keep the first one
            old = seen[key]
            old.last_seen = max(old.last_seen, r["last_seen"] or r["found_at"])
            old.thumb = old.thumb or r["thumb"]
            continue
        g = sisters.group_of(r["domain"] or "")
        seen[key] = Find(
            id=r["id"], match_id=match_id(r["id"], r["found_at"]), found_at=r["found_at"],
            last_seen=r["last_seen"] or r["found_at"], page=page, image_url=r["image_url"],
            domain=r["domain"] or "", company=g.name if g else (r["domain"] or ""),
            image_id=r["image_id"], our_path=r["our_path"] or "", photographer=r["photographer"] or "(unknown)",
            year=r["year"], quarter=r["quarter"] or "", category=r["category"], thumb=r["thumb"],
            licensed=approvals.licensed(r["photographer"], r["domain"] or "") if r["page_url"] else None,
        )
    finds = list(seen.values())
    photos: dict[int, Photo] = {}
    for f in finds:
        if f.licensed:
            continue
        p = photos.setdefault(f.image_id, Photo(f.image_id, f.our_path, f.photographer, f.year, f.quarter))
        p.finds.append(f)
    return finds, photos, sisters


def _plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def render_summary(store, data_dir) -> str:
    finds, photos, sisters = collect(store, data_dir)
    stolen = [f for f in finds if not f.licensed]
    licensed = [f for f in finds if f.licensed]
    repeats = sorted((p for p in photos.values() if len(p.pages) > 1),
                     key=lambda p: (-len(p.pages), -len(p.sites), p.our_path))
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>PixelProof summary</title><style>{CSS}{SUMMARY_CSS}</style></head><body><div class=wrap>"]
    out.append("<div class=brand><b>Pixel</b><i>Proof</i> &middot; Everything found so far</div>")
    out.append("<h1>Summary of all checks</h1>")
    out.append(f"<div class=sub>Made {_e(_when(datetime.now().astimezone().isoformat(timespec='seconds')))}</div>")
    out.append("<div class=stats>"
               f"<div class=stat><div class=n>{store.count_pages():,}</div><div class=l>pages checked</div></div>"
               f"<div class='stat hit'><div class=n>{len(photos):,}</div><div class=l>of your photos found</div></div>"
               f"<div class=stat><div class=n>{len({f.page for f in stolen}):,}</div><div class=l>pages using them</div></div>"
               f"<div class=stat><div class=n>{len({f.domain for f in stolen}):,}</div><div class=l>sites</div></div>"
               f"<div class='stat hit'><div class=n>{len(repeats):,}</div><div class=l>photos on 2+ pages</div></div>"
               f"<div class=stat><div class=n>{len(sisters.groups):,}</div><div class=l>sister-site groups</div></div>"
               f"<div class=stat><div class=n>{len(licensed):,}</div><div class=l>licensed uses</div></div>"
               "</div>")
    if not finds:
        out.append("<div class=empty><b>Nothing found yet</b>Check some pages first (menu options 2 and 4).</div>")

    # 1. Repeat copies ------------------------------------------------------
    out.append("<h2>Same photo on many pages</h2>")
    if not repeats:
        out.append("<p class=sub>None yet. When one of your photos turns up on more than one page, it is grouped here.</p>")
    for p in repeats[:MAX_REPEAT_CARDS]:
        thumb = _our_thumb(p.our_path) if p.our_path else None
        img = f"<img src='{thumb}' alt='our image'>" if thumb else "<div class=none>Preview not available</div>"
        where = " &middot; ".join(_e(x) for x in (p.photographer, p.year, p.quarter) if x)
        by_company: dict[str, list[Find]] = {}
        for f in sorted(p.finds, key=lambda f: f.found_at):
            by_company.setdefault(f.company, []).append(f)
        blocks = []
        for company, fs in by_company.items():
            items = "".join(
                f"<li><b>{_e(f.domain)}</b> &middot; <a href='{_e(f.page)}' target=_blank rel=noopener>{_e(f.page)}</a>"
                f" &middot; <span class=mid>{_e(f.match_id)}</span> &middot; {_e(_when(f.found_at))}</li>" for f in fs)
            blocks.append(f"<div class=site><h3>{_e(company)}</h3><ul>{items}</ul></div>")
        minis_html = ""
        imgs = [f for f in p.finds if f.thumb][:MAX_THEIR_THUMBS]
        if imgs:
            minis_html = ("<div class=minis>" + "".join(
                f"<img src='{_b64(f.thumb)}' alt='their copy' title='{_e(f.domain)}'>" for f in imgs) + "</div>")
        out.append(f"""
<div class=card>
  <div class=head><span><span class=count>{len(p.pages)}</span> pages &middot; {_plural(len(p.sites), 'site')}</span>
    <span class=mid>{_e(Path(p.our_path).name)}</span></div>
  <div class=rep>
    <div class='side ours'><span class=tag>Ours</span><div class=imgbox>{img}</div>
      <dl><dt>Photographer</dt><dd>{where or '&ndash;'}</dd></dl></div>
    <div class='side theirs'><span class=tag>Found on</span>{''.join(blocks)}{minis_html}</div>
  </div>
</div>""")
    if len(repeats) > MAX_REPEAT_CARDS:
        out.append(f"<p class=sub>Showing the top {MAX_REPEAT_CARDS} of {len(repeats):,}.</p>")

    # 2. Sister sites -------------------------------------------------------
    out.append("<h2>Sister sites (one company, many sites)</h2>")
    if not sisters.groups:
        out.append("<p class=sub>None yet. When two sites have a page with the same link ending "
                   "(like <code>/ixp/993/p/some-article/</code>), they are grouped here. "
                   "You can also name a company in sister-sites.txt (menu option 11).</p>")
    for g in sisters.groups:
        gf = [f for f in stolen if f.company == g.name]
        chips = "".join(f"<span class=chip>{_e(d)}</span>" for d in g.domains)
        shared = ""
        if g.shared:
            rows = []
            for key, doms in list(g.shared.items())[:15]:
                links = " &middot; ".join(f"<a href='{_e(u)}' target=_blank rel=noopener>{_e(d)}</a>" for d, u in doms.items())
                rows.append(f"<li><code>{_e(key)}</code><br>{links}</li>")
            more = f"<li>and {len(g.shared) - 15} more</li>" if len(g.shared) > 15 else ""
            shared = f"<details class=more open><summary>{_plural(len(g.shared), 'shared article')}</summary><ul>{''.join(rows)}{more}</ul></details>"
        how = "You named it in sister-sites.txt." if g.named else "Found because the sites share link endings."
        out.append(f"""
<div class='card grp'>
  <div class=head><b>{_e(g.name)}</b><span class=sub>{_plural(len(g.domains), 'site')} &middot;
    {_plural(len({f.image_id for f in gf}), 'photo')} of yours on {_plural(len({f.page for f in gf}), 'page')}</span></div>
  <div class=body><div class=chips>{chips}</div><div class=sub style='margin-top:6px'>{how}</div>{shared}</div>
</div>""")

    # 3. Every match, grouped by site --------------------------------------
    out.append("<h2>Every match, by site</h2>")
    if not stolen:
        out.append("<p class=sub>No matches yet.</p>")
    by_site_all: dict[str, list[Find]] = {}
    for f in stolen:
        by_site_all.setdefault(f.domain, []).append(f)
    shown = 0
    for dom, fs in sorted(by_site_all.items(), key=lambda kv: -len(kv[1])):
        rows = []
        for f in sorted(fs, key=lambda f: (f.page, f.found_at)):
            if shown >= MAX_MATCH_ROWS:
                break
            shown += 1
            ours = _our_thumb(f.our_path) if f.our_path else None
            ours_html = f"<img src='{ours}' alt='ours'>" if ours else "<span class=sub>no preview</span>"
            theirs_html = f"<img src='{_b64(f.thumb)}' alt='theirs'>" if f.thumb else "<span class=sub>check again to save</span>"
            rows.append(
                f"<tr><td class=pv>{ours_html}</td><td class=pv>{theirs_html}</td>"
                f"<td><b>{_e(Path(f.our_path).name)}</b><br><span class=sub>{_e(f.photographer)}</span></td>"
                f"<td><a href='{_e(f.page)}' target=_blank rel=noopener>{_e(f.page)}</a></td>"
                f"<td class=mid>{_e(f.match_id)}<br>{_e(_when(f.found_at))}</td></tr>")
        company = fs[0].company if fs[0].company != dom else ""
        out.append(f"<details class=sect open><summary>{_e(dom)}{(' &middot; ' + _e(company)) if company else ''}"
                   f" ({_plural(len({f.image_id for f in fs}), 'photo')})</summary><div class=tablewrap><table>"
                   "<tr><th>Ours</th><th>Theirs</th><th>Photo</th><th>Page</th><th>Match ID / found</th></tr>"
                   + "".join(rows) + "</table></div></details>")
    if shown >= MAX_MATCH_ROWS:
        out.append(f"<p class=sub>Showing the first {MAX_MATCH_ROWS:,} matches.</p>")

    # 4. By photographer and by site ----------------------------------------
    out.append("<h2>By photographer</h2>")
    people: dict[str, list[Find]] = {}
    for f in finds:
        people.setdefault(f.photographer, []).append(f)
    rows = []
    for who, fs in sorted(people.items(), key=lambda kv: -len(kv[1])):
        st = [f for f in fs if not f.licensed]
        rows.append(f"<tr><td>{_e(who)}</td><td class=n>{len({f.image_id for f in st}):,}</td>"
                    f"<td class=n>{len({f.page for f in st}):,}</td><td class=n>{len({f.domain for f in st}):,}</td>"
                    f"<td class=n>{len(fs) - len(st):,}</td></tr>")
    if rows:
        out.append("<div class=tablewrap><table><tr><th>Photographer</th><th>Photos found</th><th>Pages</th>"
                   "<th>Sites</th><th>Licensed uses</th></tr>" + "".join(rows) + "</table></div>")

    out.append("<h2>By site</h2>")
    by_site: dict[str, list[Find]] = {}
    for f in stolen:
        by_site.setdefault(f.domain, []).append(f)
    rows = []
    for dom, fs in sorted(by_site.items(), key=lambda kv: -len(kv[1])):
        company = fs[0].company if fs[0].company != dom else ""
        who = ", ".join(sorted({f.photographer for f in fs}))
        rows.append(f"<tr><td>{_e(dom)}</td><td>{_e(company)}</td><td class=n>{len({f.image_id for f in fs}):,}</td>"
                    f"<td class=n>{len({f.page for f in fs}):,}</td><td>{_e(who)}</td></tr>")
    if rows:
        out.append("<div class=tablewrap><table><tr><th>Site</th><th>Company (sister sites)</th><th>Photos</th>"
                   "<th>Pages</th><th>Photographers</th></tr>" + "".join(rows) + "</table></div>")

    # 5. Licensed -----------------------------------------------------------
    if licensed:
        items = "".join(
            f"<tr><td>{_e(f.photographer)}</td><td>{_e(f.domain)}</td><td>{_e(f.licensed)}</td>"
            f"<td><a href='{_e(f.page)}' target=_blank rel=noopener>{_e(Path(f.our_path).name or f.page)}</a></td>"
            f"<td class=mid>{_e(f.match_id)}</td></tr>" for f in licensed)
        out.append(f"<details class=sect><summary>Licensed uses ({len(licensed):,})</summary>"
                   "<div class=tablewrap><table><tr><th>Photographer</th><th>Site</th><th>Approved as</th><th>Photo</th>"
                   f"<th>Match ID</th></tr>{items}</table></div></details>")
    out.append("</div></body></html>")
    return "".join(out)


def write_summary(store, data_dir) -> Path:
    path = Path(data_dir) / "reports" / "summary.html"
    path.write_text(render_summary(store, data_dir), encoding="utf-8")
    return path
