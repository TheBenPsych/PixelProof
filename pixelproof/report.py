"""Writes the check report: a web page on your PC showing ours vs theirs side by side."""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from pathlib import Path

from .fingerprint import open_image, thumbnail_jpeg
from .matcher import CATEGORY_LABELS
from .store import match_id

CSS = """
:root{--bg:#F4F7F8;--card:#fff;--fg:#14202A;--muted:#5B6B75;--line:#DCE3E7;
--ours:#0E8A8F;--ours-soft:#DDF3F3;--theirs:#D6336C;--theirs-soft:#FBE3EC;
--exact:#0E8A8F;--edited:#5B4FD6;--possible:#C77700}
@media (prefers-color-scheme:dark){:root{--bg:#0E151A;--card:#162129;--fg:#E5EDF1;--muted:#97A9B3;
--line:#26343D;--ours:#4FC9CD;--ours-soft:#123437;--theirs:#F06A98;--theirs-soft:#3B1727;
--exact:#4FC9CD;--edited:#9A90FF;--possible:#F0A63A}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px 16px 64px}
.brand{font-weight:700;letter-spacing:.02em;color:var(--muted);font-size:13px;text-transform:uppercase}
.brand b{color:var(--ours)} .brand i{font-style:normal;color:var(--theirs)}
h1{font-size:26px;margin:6px 0 4px;line-height:1.2;word-break:break-word}
a{color:var(--ours)}
.sub{color:var(--muted)}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 26px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 18px;min-width:130px}
.stat .n{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
.stat .l{color:var(--muted);font-size:13px}
.stat.hit .n{color:var(--theirs)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:0 0 18px;overflow:hidden}
.head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;padding:12px 18px;border-bottom:1px solid var(--line)}
.pill{font-weight:600;font-size:13px;padding:3px 12px;border-radius:99px;color:#fff}
.p-exact{background:var(--exact)} .p-edited{background:var(--edited)} .p-possible{background:var(--possible)}
.mid{font-family:Consolas,ui-monospace,monospace;font-size:13px;color:var(--muted)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:0}
@media (max-width:700px){.pair{grid-template-columns:1fr}}
.side{padding:16px 18px;min-width:0}
.side+.side{border-left:1px solid var(--line)}
@media (max-width:700px){.side+.side{border-left:0;border-top:1px solid var(--line)}}
.tag{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 10px;border-radius:99px;margin-bottom:10px}
.ours .tag{background:var(--ours-soft);color:var(--ours)} .theirs .tag{background:var(--theirs-soft);color:var(--theirs)}
.imgbox{border-radius:10px;overflow:hidden;background:repeating-conic-gradient(#8881 0 25%,transparent 0 50%) 50%/16px 16px;
display:flex;align-items:center;justify-content:center;height:300px}
.ours .imgbox{outline:3px solid var(--ours-soft)} .theirs .imgbox{outline:3px solid var(--theirs-soft)}
.imgbox img{max-width:100%;max-height:300px;display:block}
.imgbox .none{color:var(--muted);padding:20px;text-align:center}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:12px 0 0;font-size:14px}
dt{color:var(--muted)} dd{margin:0;word-break:break-all}
.why{padding:10px 18px;background:var(--bg);color:var(--muted);font-size:14px;border-top:1px solid var(--line)}
.why b{color:var(--fg)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:16px;padding:40px;text-align:center;color:var(--muted)}
.empty b{display:block;font-size:20px;color:var(--fg);margin-bottom:6px}
details.more{margin-top:10px;color:var(--muted);font-size:14px} summary{cursor:pointer}
details.more ul{margin:6px 0 0;padding-left:18px} details.more li{word-break:break-all}
.p-licensed{background:var(--muted)}
.card.lic{opacity:.92} .card.lic .theirs .tag{background:var(--line);color:var(--muted)}
.note{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--edited);border-radius:12px;padding:12px 18px;margin:0 0 18px}
.note b.t{display:block;margin-bottom:2px}
.note ul{margin:6px 0 0;padding-left:18px} .note li{word-break:break-all}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.chip{background:var(--bg);border:1px solid var(--line);border-radius:99px;padding:1px 10px;font-size:13px}
.chip.me{border-color:var(--theirs);color:var(--theirs);font-weight:600}
.seen{padding:10px 18px;border-top:1px solid var(--line);font-size:14px}
.seen b{color:var(--theirs)} .seen ul{margin:6px 0 0;padding-left:18px} .seen li{word-break:break-all}
h2{font-size:19px;margin:30px 0 12px}
details.sect>summary{font-size:19px;font-weight:600;margin:30px 0 12px}
.btn{display:inline-block;background:var(--theirs);color:#fff;text-decoration:none;font-weight:600;padding:8px 16px;border-radius:10px;margin-top:10px}
"""


def _b64(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def _our_thumb(path: str) -> str | None:
    try:
        img, _ = open_image(path)
        return _b64(thumbnail_jpeg(img))
    except Exception:
        return None


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    except Exception:
        return iso


def _page_li(url, domain, extra="") -> str:
    return (f"<li><b>{_e(domain)}</b> &middot; <a href='{_e(url)}' target=_blank rel=noopener>{_e(url)}</a>"
            f"{(' &middot; ' + extra) if extra else ''}</li>")


def _sister_note(result) -> str:
    """Banner at the top: this site belongs to a company that owns other sites too."""
    sg, pages = result.sister_group, result.sister_pages
    if not sg and not pages:
        return ""
    out = ["<div class=note>"]
    if sg:
        chips = "".join(f"<span class='chip{' me' if d == result.domain else ''}'>{_e(d)}</span>" for d in sg.domains)
        how = "you listed them in sister-sites.txt" if sg.named else "they share the same link endings"
        out.append(f"<b class=t>Sister site: part of &ldquo;{_e(sg.name)}&rdquo;</b>"
                   f"<span class=sub>These sites look like one company ({how}).</span><div class=chips>{chips}</div>")
    if pages:
        items = "".join(_page_li(p["url"], p["domain"], f"checked {_e(_when(p['first_checked']))}, "
                                 f"{p['found'] or 0} of your images found") for p in pages)
        out.append(f"<div style='margin-top:8px'>The same article (same link ending) is also on:</div><ul>{items}</ul>")
    out.append("</div>")
    return "".join(out)


def _seen_strip(g) -> str:
    """Repeat copies: other pages where this same photo of ours was found before."""
    if not g.elsewhere:
        return ""
    pages: dict[str, object] = {}
    for f in g.elsewhere:
        pages.setdefault(f["page_url"] or f["image_url"], f)
    sites = {f["domain"] for f in pages.values()}
    items = "".join(
        _page_li(url, f["domain"], f"<span class=mid>{_e(match_id(f['id'], f['found_at']))}</span> "
                 f"found {_e(_when(f['found_at']))}")
        for url, f in list(pages.items())[:10])
    more = f"<li>and {len(pages) - 10} more (see the summary, menu option 10)</li>" if len(pages) > 10 else ""
    return (f"<div class=seen><details><summary><b>Repeat copy:</b> this photo was also found on "
            f"{len(pages)} other page{'s' if len(pages) != 1 else ''} ({len(sites)} site{'s' if len(sites) != 1 else ''})"
            f"</summary><ul>{items}{more}</ul></details></div>")


def _card(g, result, thumbs) -> str:
    q, _ = g.main
    c, row = g.best, g.row
    extra = g.copies[1:]
    more = ""
    if extra:
        links = "".join(f"<li><a href='{_e(eq.source)}' target=_blank rel=noopener>{_e(eq.source)}</a> "
                        f"({_e(eq.width)} &times; {_e(eq.height)})</li>" for eq, _ in extra)
        more = (f"<details class=more><summary>Also on this page in {len(extra)} other "
                f"size{'s' if len(extra) > 1 else ''}</summary><ul>{links}</ul></details>")
    if row["path"] not in thumbs:
        thumbs[row["path"]] = _our_thumb(row["path"])
    ours = thumbs[row["path"]]
    ours_img = f"<img src='{ours}' alt='our image'>" if ours else "<div class=none>Preview not available (was the file moved or renamed?)</div>"
    where = " &middot; ".join(_e(x) for x in (row["photographer"], row["year"], row["quarter"]) if x)
    pill = f"<span class='pill p-{c.category}'>{CATEGORY_LABELS[c.category]}</span>"
    if g.licensed:
        pill = (f"<span class='pill p-licensed'>Licensed for {_e(row['photographer'])} ({_e(g.licensed)})</span> "
                + pill)
    return f"""
<div class='card{' lic' if g.licensed else ''}'>
  <div class=head><span>{pill}</span>
    <span class=mid>Match ID {_e(c.match_id)}</span></div>
  <div class=pair>
    <div class='side ours'><span class=tag>Ours</span>
      <div class=imgbox>{ours_img}</div>
      <dl><dt>File</dt><dd>{_e(Path(row['path']).name)}</dd>
          <dt>Photographer</dt><dd>{where or '&ndash;'}</dd>
          <dt>Date in name</dt><dd>{_e(row['file_date'] or '–')}</dd>
          <dt>Size</dt><dd>{_e(row['width'])} &times; {_e(row['height'])}</dd>
          <dt>Folder</dt><dd>{_e(str(Path(row['path']).parent))}</dd></dl>
    </div>
    <div class='side theirs'><span class=tag>Theirs</span>
      <div class=imgbox><img src='{_b64(q.thumb)}' alt='their image'></div>
      <dl><dt>Site</dt><dd>{_e(result.domain)}</dd>
          <dt>Image link</dt><dd><a href='{_e(q.source)}' target=_blank rel=noopener>{_e(q.source)}</a></dd>
          <dt>Size</dt><dd>{_e(q.width)} &times; {_e(q.height)}</dd>
          <dt>First found</dt><dd>{_e(_when(g.found_at or result.checked_at))}</dd></dl>
      {more}
      {f"<a class=btn href='{_e(result.page_url)}' target=_blank rel=noopener>Open where found</a>" if result.page_url else ""}
    </div>
  </div>
  {_seen_strip(g)}
  <div class=why><b>Why:</b> {_e(c.note)}. Picture similarity {c.clip_sim:.2f} (1.00 = identical), fingerprint difference {c.hash_dist} of 64 bits.</div>
</div>"""


def render(result) -> str:
    groups = result.groups
    n_copies = sum(len(g.copies) for g in groups)
    title = result.page_url or result.target
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>PixelProof check: {_e(result.domain or 'file')}</title><style>{CSS}</style></head><body><div class=wrap>"]
    out.append("<div class=brand><b>Pixel</b><i>Proof</i> &middot; Check one page</div>")
    out.append(f"<h1>{_e(result.domain or Path(result.target).name)}</h1>")
    if result.page_url:
        out.append(f"<div class=sub><a href='{_e(result.page_url)}' target=_blank rel=noopener>{_e(result.page_url)}</a></div>")
    else:
        out.append(f"<div class=sub>{_e(result.target)}</div>")
    out.append(f"<div class=sub>Checked {_e(_when(result.checked_at))}</div>")

    if result.banned:
        out.append("<div class=empty><b>This site is on your ban list</b>It was not checked. Edit banned.txt to change that.</div>")
    elif result.error:
        out.append(f"<div class=empty><b>Something went wrong</b>{_e(result.error)}</div>")
    else:
        out.append("<div class=stats>"
                   f"<div class=stat><div class=n>{len(result.queries)}</div><div class=l>pictures checked</div></div>"
                   f"<div class='stat hit'><div class=n>{len(result.stolen)}</div><div class=l>of your images found</div></div>"
                   + (f"<div class=stat><div class=n>{len(result.licensed)}</div><div class=l>licensed uses</div></div>"
                      if result.licensed else "") +
                   f"<div class=stat><div class=n>{n_copies}</div><div class=l>copies on the page</div></div>"
                   f"<div class=stat><div class=n>{result.skipped_small}</div><div class=l>tiny images skipped</div></div>"
                   "</div>")
        out.append(_sister_note(result))
        if not groups:
            out.append("<div class=empty><b>No matches found</b>None of the pictures on this page look like images in your library.</div>")
        elif not result.stolen:
            out.append("<div class=empty><b>Only licensed uses found</b>Every match here is on a site approved for that photographer.</div>")
        thumbs: dict[str, str | None] = {}
        for g in result.stolen:
            out.append(_card(g, result, thumbs))
        if result.licensed:
            out.append(f"<details class=sect><summary>Licensed use ({len(result.licensed)}): "
                       "approved in approved.txt for the photographer</summary>")
            for g in result.licensed:
                out.append(_card(g, result, thumbs))
            out.append("</details>")
        if result.failed:
            out.append(f"<p class=sub>{result.failed} pictures on the page could not be downloaded or read.</p>")
    out.append("</div></body></html>")
    return "".join(out)


def write_report(result, data_dir) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = re.sub(r"[^A-Za-z0-9.-]+", "-", result.domain or Path(result.target).stem)[:60] or "check"
    path = Path(data_dir) / "reports" / f"check_{stamp}_{name}.html"
    path.write_text(render(result), encoding="utf-8")
    return path
