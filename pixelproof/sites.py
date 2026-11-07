"""Site rules: licensed sites per photographer, and sister sites owned by one company.

data\\approved.txt       Photographer = site, site ...   (their matches there show as "Licensed")
data\\sister-sites.txt   Company name = site, site ...   (sites owned by one company)

PixelProof also spots sister sites by itself: when two different sites have a page with the
same link ending (for example /ixp/993/p/anchor-tuscaloosa-apps-2027-chamber/), they are
treated as one company.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from .library import clean_photographer
from .page import domain_of

APPROVED_FILE = "approved.txt"
SISTERS_FILE = "sister-sites.txt"

APPROVED_TEMPLATE = """\
# Sites that a photographer has licensed (they are allowed to use that photographer's work).
# Matches there show up as "Licensed" instead of stolen, and only for that photographer.
#
# One line per photographer:   Photographer name = site, site, ...
# You can also use a company name from sister-sites.txt to approve all of its sites.
# Lines starting with # are ignored. Examples:
#   Yang = nypost.com
#   Maya Chen = example.com, partner-site.org
"""

SISTERS_TEMPLATE = """\
# Groups of sites that one company owns. PixelProof finds most of these by itself
# (same link ending on different sites), but you can name a company and list its sites here.
#
# One line per company:   Company name = site, site, ...
# Lines starting with # are ignored. Example:
#   Tuscaloosa radio group = catfishtuscaloosa.com, wtug.com
"""

# Link endings too short or too common to prove two sites are related.
_GENERIC_LAST = {"", "index", "index.html", "index.php", "amp", "feed", "news", "home", "default.aspx"}


def make_templates(data_dir: Path) -> None:
    for name, text in ((APPROVED_FILE, APPROVED_TEMPLATE), (SISTERS_FILE, SISTERS_TEMPLATE)):
        path = Path(data_dir) / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")


def clean_site(text: str) -> str:
    """'https://www.WTUG.com/x' or 'wtug.com' -> 'wtug.com'."""
    text = text.strip().strip('"').strip()
    if not text:
        return ""
    return domain_of(text if "//" in text else "http://" + text)


def site_matches(domain: str, site: str) -> bool:
    """True if domain is site, or a sub-site of it (news.wtug.com is part of wtug.com)."""
    return bool(site) and (domain == site or domain.endswith("." + site))


def path_key(url: str) -> str | None:
    """The link ending used to spot sister sites, or None if it is too short to mean anything.

    https://wtug.com/ixp/993/p/anchor-tuscaloosa-apps-2027-chamber/  ->  /ixp/993/p/anchor-tuscaloosa-apps-2027-chamber
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    path = unquote(urlparse(url).path or "").lower()
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or len(path) < 15 or parts[-1] in _GENERIC_LAST:
        return None
    return path


def _read_rules(path: Path) -> list[tuple[str, list[str]]]:
    """Reads 'Name = a, b, c' lines."""
    out = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        name, _, rest = line.partition("=")
        items = [x.strip() for x in re.split(r"[,;]", rest) if x.strip()]
        if name.strip() and items:
            out.append((name.strip(), items))
    return out


# ---------------------------------------------------------------- sister sites

@dataclass
class SisterGroup:
    name: str                          # company name, or "Sister sites: a.com + b.com"
    domains: list[str]
    named: bool = False                # True if you named it in sister-sites.txt
    shared: dict = field(default_factory=dict)   # link ending -> {domain: page url}


class SisterSites:
    """Joins sites into companies: your sister-sites.txt plus pages that share a link ending."""

    def __init__(self, named: list[tuple[str, list[str]]], pages: list[tuple[str, str, str]]):
        # pages: (url, domain, path_key)
        parent: dict[str, str] = {}

        def find(d):
            parent.setdefault(d, d)
            while parent[d] != d:
                parent[d] = parent[parent[d]]
                d = parent[d]
            return d

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        self._names: dict[str, str] = {}
        for name, sites in named:
            sites = [clean_site(s) for s in sites if clean_site(s)]
            for s in sites:
                find(s)
                self._names.setdefault(s, name)
                union(sites[0], s)

        by_key: dict[str, dict[str, str]] = {}
        for url, domain, key in pages:
            if key and domain:
                by_key.setdefault(key, {}).setdefault(domain, url)
        for key, doms in by_key.items():
            ds = list(doms)
            for d in ds:
                find(d)
            for d in ds[1:]:
                union(ds[0], d)

        members: dict[str, list[str]] = {}
        for d in list(parent):
            members.setdefault(find(d), []).append(d)
        self.groups: list[SisterGroup] = []
        self._of: dict[str, SisterGroup] = {}
        for ds in members.values():
            if len(ds) < 2:
                continue
            ds.sort()
            name = next((self._names[d] for d in ds if d in self._names), None)
            g = SisterGroup(name or "Sister sites: " + " + ".join(ds[:3]) + (" + more" if len(ds) > 3 else ""),
                            ds, named=bool(name))
            for d in ds:
                self._of[d] = g
            self.groups.append(g)
        for key, doms in by_key.items():
            if len(doms) > 1:
                g = self._of.get(next(iter(doms)))
                if g is not None:
                    g.shared[key] = doms
        self.groups.sort(key=lambda g: (-len(g.shared), -len(g.domains), g.name.lower()))

    def group_of(self, domain: str) -> SisterGroup | None:
        if domain in self._of:
            return self._of[domain]
        for d, g in self._of.items():
            if site_matches(domain, d):
                return g
        return None

    def by_name(self, name: str) -> SisterGroup | None:
        low = name.strip().lower()
        return next((g for g in self.groups if g.named and g.name.lower() == low), None)


def load_sisters(data_dir, store=None) -> SisterSites:
    named = _read_rules(Path(data_dir) / SISTERS_FILE)
    pages = store.page_keys() if store is not None else []
    return SisterSites(named, pages)


# ---------------------------------------------------------------- licensed (approved) sites

def _person_key(name: str | None) -> str:
    return clean_photographer(name or "").strip().lower()


class Approvals:
    """Which sites each photographer has licensed."""

    def __init__(self, rules: list[tuple[str, list[str]]], sisters: SisterSites | None = None):
        self.sisters = sisters
        self.rules: dict[str, list[str]] = {}
        for who, items in rules:
            self.rules.setdefault(_person_key(who), []).extend(items)

    def licensed(self, photographer: str | None, domain: str) -> str | None:
        """Returns what made it licensed ('nypost.com' or a company name), or None."""
        if not photographer or not domain:
            return None
        for item in self.rules.get(_person_key(photographer), []):
            g = self.sisters.by_name(item) if self.sisters is not None else None
            if g is not None:
                if any(site_matches(domain, d) for d in g.domains):
                    return g.name
                continue
            site = clean_site(item)
            if site and site_matches(domain, site):
                return site
        return None


def load_approvals(data_dir, sisters: SisterSites | None = None) -> Approvals:
    return Approvals(_read_rules(Path(data_dir) / APPROVED_FILE), sisters)


def add_approval(data_dir, photographer: str, sites: list[str]) -> list[str]:
    """Adds sites to a photographer's line in approved.txt. Returns the sites that were new."""
    path = Path(data_dir) / APPROVED_FILE
    make_templates(Path(data_dir))
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    key = _person_key(photographer)
    for i, line in enumerate(lines):
        body = line.split("#", 1)[0]
        if "=" in body and _person_key(body.partition("=")[0]) == key:
            name, _, rest = body.partition("=")
            have = [x.strip() for x in re.split(r"[,;]", rest) if x.strip()]
            low = {h.lower() for h in have}
            new = [s for s in sites if s.lower() not in low]
            if new:
                lines[i] = f"{name.strip()} = {', '.join(have + new)}"
            break
    else:
        new = list(dict.fromkeys(sites))
        lines.append(f"{clean_photographer(photographer) or photographer.strip()} = {', '.join(new)}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return new
