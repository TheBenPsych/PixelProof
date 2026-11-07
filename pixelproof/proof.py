"""Proof of Discovery: a PDF record of one match (where, when, which of our images, match ID).

Saved to data\\proofs\\<match ID>.pdf. Their picture and the page were saved the moment the
match was first found (data\\evidence\\<match ID>), so the PDF can show exactly what was found
and its SHA-256 fingerprint, even if the site changes or deletes it later.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .matcher import CATEGORY_LABELS
from .store import match_id as make_match_id

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

INK = "#191C35"
MUTED = "#62688A"
LINE = "#E1E4EF"
SOFT = "#F3F4FA"
OURS = "#0C9A9A"
THEIRS = "#D63A6E"
OK = "#1C8A57"

STATUS_TEXT = {"new": "Under review", "stolen": "Confirmed stolen", "not_match": "Marked as not a match"}


def _fonts():
    """Registers the app fonts (they cover accents and other languages). Falls back to Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    names = {"body": "Helvetica", "bold": "Helvetica-Bold", "mono": "Courier", "display": "Helvetica-Bold"}
    files = {"body": "PlusJakartaSans-Regular.ttf", "bold": "PlusJakartaSans-Bold.ttf",
             "mono": "JetBrainsMono-Regular.ttf", "display": "Unbounded-SemiBold.ttf"}
    for key, file in files.items():
        path = FONT_DIR / file
        name = "PP-" + key
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            names[key] = name
        except Exception:
            pass
    return names


def _when(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    local = d.strftime("%B %d, %Y at %I:%M:%S %p").replace(" 0", " ")
    off = d.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else ""
    utc = d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if d.tzinfo else ""
    return f"{local} ({off})  ·  {utc}" if utc else local


def _file_sha256(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _jpeg_for_pdf(source, max_side=900) -> tuple[bytes, int, int] | None:
    """Returns (jpeg bytes, width, height) of a picture, small enough for the PDF."""
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
        try:
            img.draft("RGB", (max_side, max_side))
        except Exception:
            pass
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGBA") if "A" in img.getbands() or img.mode == "P" else img.convert("RGB")
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88)
        return out.getvalue(), img.width, img.height
    except Exception:
        return None


def their_picture(row) -> bytes | str | None:
    """Their picture as saved at discovery (evidence folder), else the small copy in the database."""
    folder = row["evidence_dir"]
    if folder and os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.startswith("theirs"):
                return os.path.join(folder, name)
    return row["thumb"]


def build_pdf(row, store, data_dir, extra: dict | None = None) -> Path:
    """Writes data\\proofs\\<match ID>.pdf for one finding row (from Store.finding). Returns the path.

    extra (optional): {"company": str, "sister_sites": [..], "licensed": str|None, "other_pages": [(url, mid, when)]}
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Flowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)
    from xml.sax.saxutils import escape

    extra = extra or {}
    f = _fonts()
    mid = make_match_id(row["id"], row["found_at"])
    out_dir = Path(data_dir) / "proofs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{mid}.pdf"

    def P(text, size=9.5, font=f["body"], color=INK, leading=None, wrap=False):
        st = ParagraphStyle("p", fontName=font, fontSize=size, leading=leading or size * 1.35,
                            textColor=colors.HexColor(color), alignment=TA_LEFT,
                            wordWrap="CJK" if wrap else None)
        return Paragraph(text, st)

    def e(x):
        return escape(str(x if x is not None else "-"))

    status = row["status"] or "new"
    page = row["page_url"] or ""
    where = page or row["image_url"] or ""
    photographer = row["photographer"] or "Unknown photographer"
    cat = CATEGORY_LABELS.get(row["category"], row["category"] or "-")
    made = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    # ---------- header band ----------
    class Header(Flowable):
        def __init__(self, width):
            super().__init__()
            self.width, self.height = width, 0.95 * inch

        def draw(self):
            c = self.canv
            c.setFillColor(colors.HexColor(INK))
            c.roundRect(0, 0, self.width, self.height, 10, stroke=0, fill=1)
            s, g, x0, y0 = 9, 2, 18, self.height / 2 - 10  # the four-square logo
            for dx, dy, col in ((0, s + g, OURS), (s + g, s + g, "#FFFFFF"), (0, 0, "#FFFFFF"), (s + g, 0, THEIRS)):
                c.setFillColor(colors.HexColor(col))
                c.roundRect(x0 + dx, y0 + dy, s, s, 2, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont(f["display"], 17)
            c.drawString(x0 + 34, self.height / 2 + 1, "Proof of Discovery")
            c.setFont(f["body"], 8.5)
            c.setFillColor(colors.HexColor("#B9BEDA"))
            c.drawString(x0 + 34, self.height / 2 - 13, "PixelProof image use record")
            c.setFont(f["mono"], 13)
            w = c.stringWidth(mid, f["mono"], 13) + 22
            c.setFillColor(colors.white)
            c.roundRect(self.width - w - 16, self.height / 2 - 12, w, 24, 5, stroke=0, fill=1)
            c.setFillColor(colors.HexColor(INK))
            c.drawString(self.width - w - 5, self.height / 2 - 4.5, mid)

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.7 * inch,
                            title=f"Proof of Discovery {mid}", author="PixelProof",
                            subject=f"Use of {photographer}'s image on {row['domain'] or ''}")
    W = doc.width
    story = [Header(W), Spacer(1, 12)]

    found_txt = _when(row["found_at"]).split("  ·  ")[0]
    lead = (f"On <b>{e(found_txt)}</b>, PixelProof found a copy of <b>{e(photographer)}</b>'s photo on "
            f"<b>{e(row['domain'] or 'a file on this PC')}</b>. It was sorted as <b>{e(cat)}</b>. "
            f"Status: <b>{e(STATUS_TEXT.get(status, status))}</b>.")
    story += [P(lead, 11, leading=15.5), Spacer(1, 10)]

    # ---------- ours vs theirs ----------
    def pic(source, label, color, caption):
        got = _jpeg_for_pdf(source) if source else None
        box_w, box_h = (W - 14) / 2, 2.2 * inch
        if got:
            data, w, h = got
            scale = min((box_w - 12) / w, (box_h - 12) / h)
            img = RLImage(io.BytesIO(data), width=w * scale, height=h * scale)
        else:
            img = P("Picture not available", 9, color=MUTED)
        t = Table([[P(f"<b>{label}</b>", 8.5, f["bold"], color)], [img], [P(caption, 7.5, color=MUTED, wrap=True)]],
                  colWidths=[box_w], rowHeights=[16, box_h, None])
        t.setStyle(TableStyle([
            ("ALIGN", (0, 1), (0, 1), "CENTER"), ("VALIGN", (0, 1), (0, 1), "MIDDLE"),
            ("BACKGROUND", (0, 1), (0, 1), colors.HexColor(SOFT)),
            ("BOX", (0, 1), (0, 1), 2, colors.HexColor(color)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    ours_cap = f"{e(os.path.basename(row['our_path'] or ''))}"
    if row["our_w"]:
        ours_cap += f" · {row['our_w']} × {row['our_h']} px"
    theirs_cap = f"As found on {e(row['domain'] or '-')}"
    if row["width"]:
        theirs_cap += f" · {row['width']} × {row['height']} px"
    pair = Table([[pic(row["our_path"], "OURS", OURS, ours_cap), pic(their_picture(row), "THEIRS", THEIRS, theirs_cap)]],
                 colWidths=[W / 2, W / 2])
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (0, 0), 7), ("LEFTPADDING", (1, 0), (1, 0), 7)]))
    story += [pair, Spacer(1, 14)]

    # ---------- details ----------
    def section(title, rows):
        data = [[P(f"<b>{title.upper()}</b>", 7.5, f["bold"], MUTED), ""]]
        for k, v, mono in rows:
            if v in (None, ""):
                continue
            data.append([P(e(k), 8.5, color=MUTED),
                         P(e(v), 8 if mono else 9, f["mono"] if mono else f["body"], wrap=mono)])
        t = Table(data, colWidths=[1.55 * inch, W - 1.55 * inch])
        t.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor(LINE)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        return KeepTogether([t, Spacer(1, 7)])

    sim = f"{round((row['clip_sim'] or 0) * 100)}% alike"
    hd = f"hash difference {row['hash_dist']} of 64" if row["hash_dist"] is not None else ""
    period = " ".join(str(x) for x in (row["year"], row["quarter"]) if x)
    sisters = [d for d in extra.get("sister_sites") or [] if d != row["domain"]]
    story.append(section("Where it was found", [
        ("Page (infringing URL)", page or "(a picture file checked on this PC)", True),
        ("Picture link", row["image_url"] if row["image_url"] != page else None, True),
        ("Website", row["domain"], True),
        ("Company / sister sites", (extra.get("company") or "") + (f": {', '.join(sisters)}" if sisters else "")
         if (extra.get("company") or sisters) else None, False),
    ]))
    story.append(section("When", [
        ("First found", _when(row["found_at"]), False),
        ("Last seen", _when(row["last_seen"]) if row["last_seen"] and row["last_seen"] != row["found_at"] else None, False),
        ("Times seen", str(row["times_seen"] or 1), False),
    ]))
    story.append(section("Our image", [
        ("Photographer", photographer, False),
        ("Year / quarter", period or None, False),
        ("Photo date (from file name)", row["file_date"], False),
        ("File on this PC", row["our_path"], True),
        ("File SHA-256", _file_sha256(row["our_path"]) if row["our_path"] else None, True),
    ]))
    their_src = their_picture(row)
    story.append(section("Their copy (saved when found)", [
        ("Saved copy", their_src if isinstance(their_src, str) else "small preview only (found before evidence saving existed)", True),
        ("Picture SHA-256", row["their_sha256"], True),
        ("Page saved as", os.path.join(row["evidence_dir"], "page.html") if row["evidence_dir"] and row["page_sha256"] else None, True),
        ("Page SHA-256", row["page_sha256"], True),
    ]))
    story.append(section("Match", [
        ("Match ID", mid, True),
        ("Match type", f"{cat} · {sim}" + (f" · {hd}" if hd else ""), False),
        ("Notes", row["note"], False),
        ("Status", STATUS_TEXT.get(status, status) + (f" on {_when(row['status_at']).split('  ·  ')[0]}"
                                                      if status != "new" and row["status_at"] else ""), False),
        ("Licensed", f"Yes, this site is approved for {photographer} ({extra['licensed']})" if extra.get("licensed") else None, False),
    ]))
    others = extra.get("other_pages") or []
    if others:
        rows = [(m, u, True) for u, m, _ in others[:15]]
        if len(others) > 15:
            rows.append(("", f"and {len(others) - 15} more", False))
        story.append(section(f"Same photo also found on {len(others)} other page{'s' if len(others) != 1 else ''}", rows))

    story.append(Spacer(1, 4))
    story.append(P(
        f"For a full-page capture of the site, use PageProof with case number <b>{e(mid)}</b>. "
        f"This record was made by PixelProof {e(config.VERSION)} on computer {e(platform.node() or '-')} "
        f"at {e(_when(made).split('  ·  ')[0])}. Their picture and the page were saved the moment the match was "
        f"first found and are kept with this match ID; the SHA-256 fingerprints above show they have not changed since.",
        7.8, color=MUTED, leading=11))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(f["body"], 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(d.leftMargin, 0.4 * inch, f"PixelProof · Proof of Discovery · {mid}")
        canvas.drawRightString(d.leftMargin + d.width, 0.4 * inch, f"Page {d.page}")
        canvas.restoreState()

    tmp = path.with_suffix(".tmp")
    doc.filename = str(tmp)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    os.replace(tmp, path)
    store.save_proof(row["id"], str(path), _file_sha256(str(path)) or "")
    return path
