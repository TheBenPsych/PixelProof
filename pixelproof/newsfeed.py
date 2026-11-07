"""News feed: finds NEW sites using your pictures, from the free GDELT project.

GDELT (gdeltproject.org) reads news sites around the world and publishes, every 15 minutes, a list of the
new articles it saw, with each article's main picture. PixelProof downloads those lists, checks every
main picture against your library, and when one is yours it checks the whole article (saving evidence like
"Check one page") and adds the site to your watch list, so the crawler visits it from then on.

No account or key is needed. Each 15-minute list is a few MB.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import config
from .fingerprint import open_image, thumbnail_jpeg
from .matcher import match_images, match_prepared
from .prep import Prepared, prepare_many
from .page import domain_of, is_banned, load_banned, session
from .sites import is_unlikely

GDELT = "http://data.gdeltproject.org/gdeltv2/"
# GKG 2.1 columns (tab separated, no header row)
COL_SOURCE, COL_URL, COL_IMAGE = 3, 4, 18

# "US only" (Puerto Rico included) is decided by the site's ending. .com/.org/.net are mostly US but not always,
# so well-known foreign .com news sites are left out by name.
US_ENDINGS = (".com", ".org", ".net", ".us", ".pr", ".news", ".tv", ".fm", ".am", ".radio", ".media",
              ".nyc", ".la", ".info", ".co", ".io")
NOT_US = set("""
aljazeera.com scmp.com rt.com thestar.com indiatimes.com hindustantimes.com ndtv.com thehindu.com
news18.com firstpost.com indianexpress.com livemint.com moneycontrol.com business-standard.com dnaindia.com
zeenews.com republicworld.com deccanherald.com tribuneindia.com newindianexpress.com thequint.com
straitstimes.com channelnewsasia.com japantimes.co.jp koreaherald.com koreatimes.co.kr globaltimes.cn
chinadaily.com.cn xinhuanet.com dawn.com geo.tv tribune.com.pk arabnews.com gulfnews.com khaleejtimes.com
thenationalnews.com jpost.com timesofisrael.com haaretz.com dailysabah.com hurriyetdailynews.com
euronews.com france24.com dw.com rfi.fr thelocal.com politico.eu bangkokpost.com philstar.com inquirer.net
rappler.com manilatimes.net thejakartapost.com malaymail.com thestar.com.my nzherald.co.nz stuff.co.nz
smh.com.au theage.com.au news.com.au skynews.com.au theglobeandmail.com nationalpost.com ctvnews.ca
globalnews.ca torontosun.com punchng.com vanguardngr.com premiumtimesng.com allafrica.com news24.com
iol.co.za businessday.ng nation.africa standardmedia.co.ke ghanaweb.com myjoyonline.com
shafaqna.com presstv.ir tehrantimes.com
""".split())


def is_us(domain: str) -> bool:
    d = domain.lower()
    if any(d == x or d.endswith("." + x) for x in NOT_US):
        return False
    return d.endswith(US_ENDINGS)


def latest_stamp(sess) -> str:
    """The time stamp of the newest 15-minute list, like 20261107031500."""
    r = sess.get(GDELT + "lastupdate.txt", timeout=30)
    r.raise_for_status()
    for line in r.text.splitlines():
        url = line.split()[-1] if line.split() else ""
        if url.endswith(".gkg.csv.zip"):
            return url.rsplit("/", 1)[1].split(".")[0]
    raise RuntimeError("GDELT's update list did not name a file")


def stamps_back(latest: str, hours: float) -> list[str]:
    """Every 15-minute stamp from `latest` back `hours`, newest first."""
    t = datetime.strptime(latest, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    n = max(1, int(hours * 4))
    return [(t - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(n)]


def read_list(sess, stamp: str) -> list[tuple[str, str, str]]:
    """Downloads one 15-minute list. Returns (site, article link, main picture link) for each article."""
    r = sess.get(f"{GDELT}{stamp}.gkg.csv.zip", timeout=120)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0])
    return parse_gkg(raw)


def parse_gkg(raw: bytes) -> list[tuple[str, str, str]]:
    out, seen = [], set()
    for line in raw.decode("utf-8", "replace").splitlines():
        cols = line.split("\t")
        if len(cols) <= COL_IMAGE:
            continue
        url, img = cols[COL_URL].strip(), cols[COL_IMAGE].strip()
        if not url.startswith("http") or not img.startswith("http") or img in seen:
            continue
        seen.add(img)
        site = domain_of(url) or cols[COL_SOURCE].strip().lower()
        out.append((site, url, img))
    return out


def _download(sess, url):
    try:
        with sess.get(url, timeout=(5, 12), stream=True) as r:
            r.raise_for_status()
            data = bytearray()
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > config.MAX_WEB_IMAGE_BYTES:
                    return None
            return bytes(data)
    except Exception:
        return None


def scan(store, clip, data_dir, hours=3, us_only=True, log=print, should_stop=lambda: False,
         on_progress=None, add_watch=None, sess=None, stamps=None) -> dict:
    """Checks the main picture of every news article GDELT saw in the last `hours`.
    Returns totals plus the new match IDs and the sites added to the watch list."""
    from .check import check_target

    sess = sess or session()
    done = set(json.loads(store.get_meta("gdelt_done", "[]")))
    if stamps is None:
        stamps = stamps_back(latest_stamp(sess), hours)
    todo = [s for s in stamps if s not in done]
    res = {"lists": 0, "articles": 0, "pictures": 0, "matches": 0, "new_ids": [], "new_sites": [],
           "skipped_lists": len(stamps) - len(todo)}
    if res["skipped_lists"]:
        log(f"{res['skipped_lists']} of the {len(stamps)} news lists were already checked before, skipping them.")
    banned = load_banned(f"{data_dir}/banned.txt")
    seen_pages = set()
    fast = getattr(clip, "fast", False)
    for si, stamp in enumerate(todo, 1):
        if should_stop():
            break
        try:
            items = read_list(sess, stamp)
        except Exception as e:
            log(f"Could not read the news list for {stamp}: {type(e).__name__}: {e}")
            continue
        items = [(s, u, i) for s, u, i in items
                 if (not us_only or is_us(s)) and not is_banned(u, banned) and not is_unlikely(s)]
        log(f"News list {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]} UTC: "
            f"{len(items)} articles to check.")
        t_list = time.time()
        t_cmp = 0.0
        n_pics = 0

        def fetch(it):   # download and read one picture (runs 64 at a time, while matching goes on)
            data = _download(sess, it[2])
            if not data:
                return None
            if fast:   # the picture work itself runs on every processor core at once (see prep.py)
                p = prepare_many([data], config.MIN_WEB_IMAGE_SIDE)[0]
                return (it, data, p) if isinstance(p, Prepared) else None
            try:
                img, (w, h) = open_image(data)
            except Exception:
                return None
            if min(w, h) < config.MIN_WEB_IMAGE_SIDE:
                return None
            return it, data, img

        with ThreadPoolExecutor(max_workers=64) as pool:
            pending = deque()
            nxt = 0
            while (nxt < len(items) or pending) and not should_stop():
                while nxt < len(items) and len(pending) < 384:   # keep downloads running ahead
                    pending.append(pool.submit(fetch, items[nxt]))
                    nxt += 1
                batch = [pending.popleft().result() for _ in range(min(64, len(pending)))]
                got = [x for x in batch if x]
                res["articles"] += len(batch)
                res["pictures"] += len(got)
                n_pics += len(got)
                t0 = time.time()
                if not got:
                    found = []
                elif fast:
                    found = match_prepared([x[2] for x in got], store, clip,
                                           lambda i, g=got: open_image(g[i][1])[0])
                else:
                    found = match_images([x[2] for x in got], store, clip)
                t_cmp += time.time() - t0
                for (it, data, _), matches in zip(got, found):
                    if not matches:
                        continue
                    site, url, img_url = it
                    if url in seen_pages:
                        continue
                    seen_pages.add(url)
                    log(f"Your image found on {site}: {url}")
                    before = store.db.execute("SELECT COALESCE(MAX(id),0) FROM findings").fetchone()[0]
                    r = check_target(url, store, clip, data_dir, log=lambda *a: None, sess=sess)
                    if r.error or not r.groups:
                        # the article page could not be read: keep the match from the feed picture itself
                        _save_feed_match(store, data_dir, site, url, img_url, matches[0], data)
                    ids = [i for (i,) in store.db.execute("SELECT id FROM findings WHERE id>?", (before,))]
                    res["matches"] += 1
                    res["new_ids"] += ids
                    if add_watch and site not in res["new_sites"] and add_watch(site):
                        res["new_sites"].append(site)
                        log(f"Added {site} to your watch list.")
                if on_progress:
                    on_progress(si, len(todo), res)
            for f in pending:
                f.cancel()
        took = max(time.time() - t_list, 0.1)
        if items:
            log(f"  done in {took:.0f}s: {n_pics} pictures read, comparing took {t_cmp:.1f}s, "
                f"{len(items) / took * 60:.0f} articles per minute.")
        if should_stop():
            break
        done.add(stamp)
        store.set_meta("gdelt_done", json.dumps(sorted(done)[-2000:]))
        store.commit()
        res["lists"] += 1
    return res


def _save_feed_match(store, data_dir, site, url, img_url, cand, data):
    from .check import CheckResult, save_evidence
    from .matcher import Query
    from .sites import path_key
    from .store import match_id, now

    img, (w, h) = open_image(data)
    fid, found_at = store.save_finding(url, img_url, site, cand.image_id, cand.category, cand.clip_sim,
                                       cand.hash_dist, cand.note + "; found through the news feed",
                                       path_key=path_key(url), thumb=thumbnail_jpeg(img), width=w, height=h)
    res = CheckResult(target=url, page_url=url, domain=site, checked_at=now())
    save_evidence(store, fid, match_id(fid, found_at), data_dir, res, Query(source=img_url), data, None)
    store.commit()
