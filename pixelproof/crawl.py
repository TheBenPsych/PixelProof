"""The crawler: visits the sites on your watch list and checks the pictures on their newest pages.

For each site it:
  1. reads robots.txt (and stays out of anything the site asks robots to avoid),
  2. finds the newest pages from the site's sitemap or RSS feed (or, failing that, its home page),
  3. checks each page it has not checked recently, exactly like "Check one page".

Matches land in Review. Pictures that turn up on many pages (logos, ads) are only checked once per run.
"""

from __future__ import annotations

import gzip
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .page import domain_of, is_banned, load_banned, session
from .store import now

# Sites far too big to crawl, or that are not normal websites (app links, file stores, link shorteners).
# Pictures there are better found with "Check one page".
TOO_BIG = set("""
google.com youtube.com facebook.com instagram.com twitter.com x.com linkedin.com tiktok.com pinterest.com
reddit.com amazon.com apple.com microsoft.com wikipedia.org wikimedia.org wiktionary.org blogger.com blogspot.com
wordpress.org wordpress.com wp.com github.com cloudflare.com googleusercontent.com gstatic.com ytimg.com
amazonaws.com akamaihd.net bit.ly t.co goo.gl youtu.be amzn.to fb.com fb.me wa.me whatsapp.com messenger.com
live.com outlook.com office.com bing.com msn.com yahoo.com aol.com gmail.com icloud.com dropbox.com mediafire.com
4shared.com sendspace.com storage.googleapis.com feedburner.com googleblog.com gravatar.com tinyurl.com
mozilla.org mozilla.com firefox.com w3.org ietf.org icann.org doi.org archive.org imdb.com netflix.com hulu.com
max.com paramountplus.com peacocktv.com ebay.com walmart.com target.com etsy.com paypal.com zoom.us skype.com
discord.com twitch.tv vimeo.com soundcloud.com spotify.com pandora.com tumblr.com imgur.com snapchat.com
threads.net bsky.app quora.com stackoverflow.com tripadvisor.com yelp.com booking.com airbnb.com craigslist.org
indeed.com glassdoor.com zillow.com realtor.com godaddy.com namecheap.com secureserver.net cpanel.net cpanel.com
adobe.com oracle.com ibm.com cisco.com hp.com salesforce.com hubspot.com zendesk.com shutterstock.com
istockphoto.com gettyimages.com flickr.com
""".split())

FEED_PATHS = ("/feed", "/rss", "/feed/", "/rss.xml", "/feeds/all.rss", "/index.xml", "/atom.xml")
SITEMAP_PATHS = ("/sitemap_index.xml", "/sitemap.xml", "/news-sitemap.xml", "/sitemap-news.xml", "/post-sitemap.xml")
SKIP_PATH_RE = re.compile(
    r"/(tag|tags|category|categories|author|authors|search|login|signin|register|account|cart|checkout|privacy|terms|"
    r"contact|about|advertise|subscribe|newsletter|wp-admin|wp-login|feed|rss|amp)(/|$)|\.(pdf|mp3|mp4|zip|xml|json|js|css)$",
    re.I)
LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)
URL_BLOCK_RE = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.I | re.S)
DATE_RE = re.compile(r"<(?:lastmod|news:publication_date|pubDate|published|updated)>\s*(?:<!\[CDATA\[)?\s*([^<\]]+)", re.I)
ITEM_RE = re.compile(r"<(item|entry)\b(.*?)</\1>", re.I | re.S)


@dataclass
class SiteResult:
    domain: str
    status: str = ""             # done | skipped | error | stopped
    note: str = ""
    found: int = 0               # pages found on the site
    checked: int = 0             # pages checked this time
    matches: int = 0             # our images found this time
    new_ids: list = field(default_factory=list)
    pictures: int = 0            # pictures looked at
    t_page: float = 0.0          # seconds spent opening pages
    t_pictures: float = 0.0      # seconds spent downloading pictures
    t_compare: float = 0.0       # seconds spent reading and comparing pictures
    t_find: float = 0.0          # seconds spent opening the site and finding its pages (sitemap, news feed)
    stats: dict = field(default_factory=dict)   # inside comparing (see check.CheckResult.stats)


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _parse_date(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:   # also odd dates like year 0001, which Windows can't turn into a time (Errno 22)
        pass
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        return 0.0


def _get(sess, url: str, timeout=(8, 20)) -> tuple[str, str] | None:
    """Returns (final url, text) or None. The reason for a None is kept in sess.px_why."""
    try:
        r = sess.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            sess.px_why = WHY.get(r.status_code, f"it answered with error {r.status_code}")
            return None
        data = r.content
        if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        return r.url, data.decode(r.encoding or "utf-8", "replace")
    except Exception as e:
        name = type(e).__name__
        sess.px_why = ("it took too long to answer" if "Timeout" in name else
                       "the address does not exist or refused the connection" if "Connection" in name else name)
        return None


WHY = {403: "it blocks robots (error 403)", 401: "it needs a login (error 401)", 404: "page not found (error 404)",
       429: "it asked us to slow down (error 429)", 451: "it is not available in your country (error 451)",
       503: "it blocks robots or is down (error 503)"}


class SiteCrawler:
    helpers = False   # read big sitemaps in a helper program (see prep.py)

    def __init__(self, domain: str, sess=None, log=print, base_url: str | None = None, delay: float = 1.0):
        self.domain = domain
        self.sess = sess or session()
        self.log = log
        self.base = base_url
        self.delay = delay
        self.robots: RobotFileParser | None = None
        self.sitemaps: list[str] = []
        self._last = 0.0

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def fetch(self, url):
        self._wait()
        return _get(self.sess, url)

    def allowed(self, url: str) -> bool:
        if self.robots is None:
            return True
        try:
            return self.robots.can_fetch("PixelProof", url) and self.robots.can_fetch("*", url)
        except Exception:
            return True

    def same_site(self, url: str) -> bool:
        d = domain_of(url)
        return d == self.domain or d.endswith("." + self.domain)

    def start(self) -> bool:
        """Finds a working home page address and reads robots.txt."""
        bases = [self.base] if self.base else [f"https://{self.domain}", f"https://www.{self.domain}", f"http://{self.domain}"]
        for b in bases:
            got = self.fetch(b.rstrip("/") + "/")
            if got:
                final, self.home_html = got
                p = urlparse(final)
                self.base = f"{p.scheme}://{p.netloc}"
                break
        else:
            return False
        rp = RobotFileParser()
        got = self.fetch(self.base + "/robots.txt")
        if got:
            lines = got[1].splitlines()
            rp.parse(lines)
            self.robots = rp
            self.sitemaps = [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")]
        return True

    # ---------- finding pages ----------
    def from_sitemaps(self, want: int) -> list[tuple[str, float]]:
        todo = list(dict.fromkeys(self.sitemaps + [self.base + p for p in SITEMAP_PATHS]))
        pages: dict[str, float] = {}
        fetched = 0
        while todo and fetched < 8 and len(pages) < want * 4:
            sm = todo.pop(0)
            got = self.fetch(sm)
            fetched += 1
            if not got or "<" not in got[1][:500]:
                continue
            if self.helpers:   # big sitemaps (tens of thousands of pages) are read by a helper (prep.py)
                from .prep import run

                is_index, entries = run(sitemap_entries, got[1])
            else:
                is_index, entries = sitemap_entries(got[1])
            if is_index:
                def score(k):  # newest first; news and post sitemaps before others
                    url, when = k
                    bonus = 1e10 if re.search(r"news|post|article|stor", url, re.I) else 0
                    return when + bonus
                entries.sort(key=score, reverse=True)
                todo = [k for k, _ in entries[:5]] + todo
                continue
            for loc, when in entries:
                pages.setdefault(loc, when)
            if pages and not self.sitemaps:
                break  # one standard sitemap is enough
        return list(pages.items())

    def from_feeds(self) -> list[tuple[str, float]]:
        feeds = re.findall(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", getattr(self, "home_html", ""), re.I)
        urls = [urljoin(self.base + "/", m.group(1)) for f in feeds for m in [re.search(r"href=[\"']([^\"']+)", f)] if m]
        urls += [self.base + p for p in FEED_PATHS]
        pages: dict[str, float] = {}
        for feed in list(dict.fromkeys(urls))[:4]:
            got = self.fetch(feed)
            if not got or not re.search(r"<(rss|feed|channel)\b", got[1][:2000], re.I):
                continue
            for _, body in ITEM_RE.findall(got[1]):
                link = None
                m = re.search(r"<link>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", body, re.I)
                if m:
                    link = m.group(1)
                else:
                    m = re.search(r"<link[^>]+href=[\"']([^\"']+)", body, re.I)
                    link = m.group(1) if m else None
                if link:
                    d = DATE_RE.search(body)
                    pages.setdefault(link.strip(), _parse_date(d.group(1)) if d else 0.0)
            if pages:
                break
        return list(pages.items())

    def from_home(self) -> list[tuple[str, float]]:
        html = getattr(self, "home_html", "")
        links = re.findall(r"<a[^>]+href=[\"']([^\"'#]+)", html, re.I)
        out = []
        for href in links:
            u = urljoin(self.base + "/", href.strip())
            path = urlparse(u).path
            # article-like: at least two folders deep, or a long slug with dashes
            if len([p for p in path.split("/") if p]) >= 2 or path.count("-") >= 3:
                out.append((u, 0.0))
        return list(dict.fromkeys(out))

    def find_pages(self, want: int) -> tuple[list[str], str]:
        for how, finder in (("sitemap", lambda: self.from_sitemaps(want)), ("news feed", self.from_feeds),
                            ("home page", self.from_home)):
            pages = finder()
            pages = [(u.split("#")[0], d) for u, d in pages
                     if u.startswith("http") and self.same_site(u) and not SKIP_PATH_RE.search(urlparse(u).path)]
            if pages:
                pages.sort(key=lambda x: x[1], reverse=True)
                return list(dict.fromkeys(u for u, _ in pages)), how
        return [], ""


def sitemap_entries(text: str) -> tuple[bool, list[tuple[str, float]]]:
    """Reads one sitemap. Returns (is it a list of other sitemaps, [(link, date as a number)])."""
    out = []
    for _, block in URL_BLOCK_RE.findall(text):
        loc = LOC_RE.search(block)
        if loc:
            d = DATE_RE.search(block)
            out.append((loc.group(1).strip(), _parse_date(d.group(1)) if d else 0.0))
    return bool(re.search(r"<sitemapindex", text, re.I)), out


def crawl_site(domain: str, store, clip, data_dir, pages_per_site=20, recheck_days=7, log=print,
               should_stop=lambda: False, seen_images=None, base_url=None, delay=1.0,
               on_page=None) -> SiteResult:
    from .check import check_target

    res = SiteResult(domain)
    if domain in TOO_BIG or any(domain.endswith("." + b) for b in TOO_BIG):
        res.status, res.note = "skipped", "too big to crawl (use Check one page there)"
        return res
    if is_banned("http://" + domain, load_banned(f"{data_dir}/banned.txt")):
        res.status, res.note = "skipped", "on your ban list"
        return res
    site = SiteCrawler(domain, log=log, base_url=base_url, delay=delay)
    site.helpers = bool(getattr(clip, "fast", False))
    t0 = time.time()
    if not site.start():
        res.status, res.note = "error", "the site did not answer: " + getattr(site.sess, "px_why", "no reason given")
        return res
    pages, how = site.find_pages(pages_per_site)
    res.t_find = time.time() - t0
    res.found = len(pages)
    if not pages:
        res.status, res.note = "done", "no article pages found (the site may need a real browser)"
        return res
    cutoff = (datetime.now(timezone.utc) - timedelta(days=recheck_days)).isoformat()
    recent = {r[0] for r in store.db.execute("SELECT url FROM pages WHERE domain=? AND last_checked>?",
                                             (domain_of(site.base), cutoff))}
    todo, blocked = [], 0
    for u in pages:
        if u in recent:
            continue
        if not site.allowed(u):
            blocked += 1
            continue
        todo.append(u)
        if len(todo) >= pages_per_site:
            break
    log(f"{domain}: found {_n(len(pages), 'page')} from its {how}; checking {len(todo)} new"
        + (f" ({blocked} skipped because the site asks robots not to visit them)" if blocked else "") + ".")
    for i, u in enumerate(todo, 1):
        if should_stop():
            res.status = "stopped"
            break
        site._wait()
        before = store.db.execute("SELECT COALESCE(MAX(id),0) FROM findings").fetchone()[0]
        r = check_target(u, store, clip, data_dir, log=lambda *a: None, seen_images=seen_images, sess=site.sess,
                         quick=True)
        res.checked += 1
        res.t_page += r.t_page
        res.t_pictures += r.t_pictures
        res.t_compare += r.t_compare
        for k, v in r.stats.items():
            res.stats[k] = res.stats.get(k, 0) + v
        res.pictures += len(r.queries) + r.failed
        if not r.error and not r.banned:
            res.matches += len(r.groups)
            res.new_ids += [g.finding_id for g in r.groups if g.finding_id > before]
            if r.groups:
                log(f"  {len(r.groups)} of your images on {u}")
        if on_page:
            on_page(i, len(todo), res)
    if not res.status:
        res.status = "done"
    if not todo and res.status == "done":
        res.note = f"no new pages since the last visit (found {_n(len(pages), 'page')} from its {how})"
    res.note = res.note or f"{_n(res.checked, 'page')} checked, found from its {how}"
    return res


def save_site(store, r: SiteResult) -> None:
    store.db.execute(
        """INSERT INTO crawl_sites(domain, last_crawl, status, note, found, checked, matches)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(domain) DO UPDATE SET last_crawl=excluded.last_crawl, status=excluded.status,
             note=excluded.note, found=excluded.found, checked=excluded.checked,
             matches=excluded.matches""",
        (r.domain, now(), r.status, r.note, r.found, r.checked, r.matches))
    store.commit()
