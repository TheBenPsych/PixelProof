"""Reads photographer, year, quarter and date from your folder and file names.

Your Drive layout is:  Photographers \\ <Name> \\ <Year> \\ <Q1..Q4> \\ file.jpg
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import IMAGE_EXTENSIONS

YEAR_RE = re.compile(r"^(19[5-9]\d|20\d\d)$")
QUARTER_RE = re.compile(r"^(?:q\s*([1-4])|([1-4])(?:st|nd|rd|th)?\s*quarter|quarter\s*([1-4]))\b", re.I)
# Folders that are containers, never a photographer's name.
CONTAINER_NAMES = {"photographers", "photos", "my drive", "shared drives", "images", "pictures"}

# Dates inside filenames, most specific first.
DATE_PATTERNS = [
    # 2026-02-14, 2026_02_14, 2026.02.14, 20260214
    re.compile(r"(?<!\d)(?P<y>(?:19|20)\d\d)[-_. ]?(?P<m>0[1-9]|1[0-2])[-_. ]?(?P<d>0[1-9]|[12]\d|3[01])(?!\d)"),
    # 02-14-2026, 02_14_2026
    re.compile(r"(?<!\d)(?P<m>0?[1-9]|1[0-2])[-_.](?P<d>0?[1-9]|[12]\d|3[01])[-_.](?P<y>(?:19|20)\d\d)(?!\d)"),
]


@dataclass
class ImageInfo:
    photographer: str | None = None
    year: int | None = None
    quarter: str | None = None
    file_date: str | None = None  # YYYY-MM-DD


def parse_quarter(name: str) -> str | None:
    m = QUARTER_RE.match(name.strip())
    if not m:
        return None
    return "Q" + next(g for g in m.groups() if g)


def date_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem
    for pattern in DATE_PATTERNS:
        for m in pattern.finditer(stem):
            try:
                d = date(int(m["y"]), int(m["m"]), int(m["d"]))
            except ValueError:
                continue
            if 1950 <= d.year <= date.today().year + 1:
                return d.isoformat()
    return None


def describe(path: str | os.PathLike, root: str | os.PathLike | None = None) -> ImageInfo:
    """Work out photographer / year / quarter / date for one image path."""
    p = Path(path)
    parts = list(p.parent.parts)
    info = ImageInfo(file_date=date_from_filename(p.name))

    # Find the year folder closest to the file.
    year_idx = None
    for i in range(len(parts) - 1, -1, -1):
        if YEAR_RE.match(parts[i].strip()):
            year_idx = i
            break

    if year_idx is not None:
        info.year = int(parts[year_idx].strip())
        if year_idx + 1 < len(parts):
            info.quarter = parse_quarter(parts[year_idx + 1])
        if year_idx > 0:
            candidate = parts[year_idx - 1].strip()
            if candidate.lower() not in CONTAINER_NAMES and not candidate.endswith((":\\", "/", ":")):
                info.photographer = candidate
    else:
        for name in reversed(parts):
            q = parse_quarter(name)
            if q:
                info.quarter = q
                break

    # Fallback: the first folder below the folder you chose to index.
    if info.photographer is None and root is not None:
        try:
            rel = p.parent.relative_to(Path(root))
            if rel.parts:
                info.photographer = rel.parts[0]
            else:
                name = Path(root).name
                if name and name.lower() not in CONTAINER_NAMES:
                    info.photographer = name
        except ValueError:
            pass

    if info.year is None and info.file_date:
        info.year = int(info.file_date[:4])
    return info


def scan(root: str | os.PathLike):
    """Yield every image file under root (skips hidden and system folders)."""
    stack = [str(root)]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        subdirs = []
        for entry in entries:
            name = entry.name
            if name.startswith((".", "~$", "$")):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)
                elif Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                    yield entry.path
            except OSError:
                continue
        stack.extend(reversed(subdirs))
