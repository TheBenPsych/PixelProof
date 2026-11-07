"""Check one page: download a web page and pull out every picture on it."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from . import config

IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|bmp|tiff?|avif|heic)(\?|#|$)", re.I)
CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
LAZY_ATTRS = ("src", "data-src", "data-lazy-src", "data-original", "data-full", "data-large-file",
              "data-orig-file", "data-hi-res-src", "data-image", "data-url")


def session():
    import requests

    from requests.adapters import HTTPAdapter

    s = requests.Session()
    s.mount("https://", HTTPAdapter(pool_connections=64, pool_maxsize=64))
    s.mount("http://", HTTPAdapter(pool_connections=64, pool_maxsize=64))
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"})
    return s


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_banned(url: str, banned: set[str]) -> bool:
    host = domain_of(url)
    return any(host == b or host.endswith("." + b) for b in banned)


def load_banned(path) -> set[str]:
    out = set()
    try:
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.add(domain_of(line if "//" in line else "http://" + line))
    except FileNotFoundError:
        pass
    return out


def _best_from_srcset(srcset: str) -> str | None:
    best, best_w = None, -1.0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 1.0
        if len(bits) > 1:
            try:
                w = float(bits[1].rstrip("wx"))
            except ValueError:
                pass
        if w > best_w:
            best, best_w = bits[0], w
    return best


def extract_image_urls(html: str, base_url: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base = urljoin(base_url, base_tag["href"]) if base_tag else base_url
    found: list[str] = []

    def add(u):
        if not u:
            return
        u = u.strip()
        if u.startswith("data:") or u.startswith("javascript:") or u.startswith("blob:"):
            return
        full = urljoin(base, u)
        if full.startswith(("http://", "https://")) and full not in found:
            found.append(full)

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            add(meta.get("content"))
    for tag in soup.find_all(["img", "source"]):
        for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
            if tag.get(attr):
                add(_best_from_srcset(tag[attr]))
        for attr in LAZY_ATTRS:
            if tag.get(attr):
                add(tag[attr])
    for a in soup.find_all("a", href=True):
        if IMG_EXT_RE.search(a["href"]):
            add(a["href"])
    for tag in soup.find_all(style=True):
        for u in CSS_URL_RE.findall(tag["style"]):
            add(u)
    for style in soup.find_all("style"):
        for u in CSS_URL_RE.findall(style.get_text()):
            if IMG_EXT_RE.search(u):
                add(u)
    for link in soup.find_all("link", href=True):
        if "image_src" in (link.get("rel") or []):
            add(link["href"])
    return found[: config.MAX_WEB_IMAGES_PER_PAGE]


def fetch_page(url: str, sess=None) -> tuple[str, str, bytes | None]:
    """Returns (final_url, html, image_bytes). If the link is an image itself, html is empty."""
    sess = sess or session()
    r = sess.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "").lower()
    if ctype.startswith("image/"):
        return r.url, "", r.content
    r.encoding = r.encoding or r.apparent_encoding
    return r.url, r.text, None


def download_images(urls: list[str], referer: str, sess=None, workers: int = 16) -> list[tuple[str, bytes | None, str]]:
    """Returns (url, bytes or None, error) in the same order."""
    sess = sess or session()

    def one(u):
        try:
            with sess.get(u, timeout=(6, 15), stream=True, headers={"Referer": referer}) as r:
                r.raise_for_status()
                data = b""
                for chunk in r.iter_content(65536):
                    data += chunk
                    if len(data) > config.MAX_WEB_IMAGE_BYTES:
                        return u, None, "too big"
                return u, data, ""
        except Exception as e:
            return u, None, f"{type(e).__name__}: {e}"[:200]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, urls))
