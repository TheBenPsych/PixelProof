"""Step 3: the desktop app engine, evidence saving and the Proof of Discovery PDF."""
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from pixelproof import app as appmod
from pixelproof.app import App, make_handler
from pixelproof.indexer import index_folder
from pixelproof.store import Store

from test_pixelproof import FakeClip, _serve, lib  # noqa: F401  (lib is a fixture)


@pytest.fixture
def engine(lib, tmp_path):  # noqa: F811
    root, files, data = lib
    store = Store(data)
    clip = FakeClip()
    index_folder(str(root), store, clip, progress=False, log=lambda *a: None)
    store.set_meta("clip_model", clip.name)
    store.commit()
    store.db.close()

    site = tmp_path / "site"
    art = site / "ixp" / "993" / "p" / "anchor-article"
    art.mkdir(parents=True)
    Image.open(files["MayaChen_2026-02-14_0031.jpg"]).resize((400, 300)).save(art / "maya.jpg", quality=70)
    Image.open(files["LO 04-03-2026 shoot.png"]).resize((400, 300)).save(art / "luis.jpg", quality=70)
    (art / "index.html").write_text("<img src='maya.jpg'><img src='luis.jpg'>")
    web = _serve(site)

    a = App(data, clip_factory=lambda d: FakeClip())
    http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(a))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{http.server_port}"
    yield a, base, web.server_port, data
    http.shutdown()
    web.shutdown()


def call(base, token, path, body=None):
    req = urllib.request.Request(base + path, headers={"X-PixelProof": token} if token else {})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw


def wait_job(base, token):
    for _ in range(100):
        j = call(base, token, "/api/job")
        if j and j["state"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_needs_the_secret_token(engine):
    a, base, _, _ = engine
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, None, "/api/state")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError):
        call(base, "wrong", "/api/state")
    page = call(base, None, f"/?t={a.token}")
    assert a.token.encode() in page and b"/ui/app.js" in page
    assert b"github" not in page.lower()


def test_check_review_proof_ban_approve(engine):
    a, base, port, data = engine
    t = a.token
    url = f"http://127.0.0.1:{port}/ixp/993/p/anchor-article/"
    assert call(base, t, "/api/check", {"targets": [url]})["ok"]
    job = wait_job(base, t)
    assert job["state"] == "done" and len(job["result"]["new_ids"]) == 2

    st = call(base, t, "/api/state")
    assert len(st["findings"]) == 2
    f = st["findings"][0]
    assert f["status"] == "new" and f["has_evidence"] and f["match_id"].startswith("PX-")
    # evidence: their picture exactly as downloaded, plus the page, with SHA-256
    row = a.store.finding(f["id"])
    assert row["their_sha256"] and row["page_sha256"]
    assert (data / "evidence" / f["match_id"] / "page.html").exists()

    # pictures for the side by side view
    assert call(base, t, f"/img/ours/{f['image_id']}?s=900")[:2] == b"\xff\xd8"
    assert call(base, t, f"/img/theirs/{f['id']}?s=900")[:2] == b"\xff\xd8"

    # confirm stolen, then the Proof of Discovery PDF
    assert call(base, t, "/api/status", {"ids": [f["id"]], "status": "stolen"})["ok"]
    res = call(base, t, "/api/proof", {"id": f["id"], "open": False})
    pdf = (data / "proofs" / (f["match_id"] + ".pdf"))
    assert res["ok"] and pdf.exists() and pdf.read_bytes().startswith(b"%PDF")
    assert a.store.finding(f["id"])["proof_sha256"]

    # checking again keeps the same match, and the evidence is not overwritten
    sha = row["their_sha256"]
    call(base, t, "/api/check", {"targets": [url]})
    job = wait_job(base, t)
    assert job["result"]["new_ids"] == [] and len(job["result"]["ids"]) == 2
    assert a.store.finding(f["id"])["their_sha256"] == sha

    # approve for one photographer -> licensed; ban -> hidden
    luis = next(x for x in st["findings"] if x["photographer"] == "Luis Ortega")
    call(base, t, "/api/approve", {"photographer": "Luis Ortega", "sites": ["127.0.0.1"]})
    st = call(base, t, "/api/state")
    assert next(x for x in st["findings"] if x["id"] == luis["id"])["licensed"] == "127.0.0.1"
    assert not next(x for x in st["findings"] if x["photographer"] == "Maya Chen")["licensed"]
    call(base, t, "/api/approve", {"photographer": "Luis Ortega", "sites": ["127.0.0.1"], "remove": True})
    assert call(base, t, "/api/state")["approved"] == []

    call(base, t, "/api/ban", {"sites": ["127.0.0.1"]})
    st = call(base, t, "/api/state")
    assert "127.0.0.1" in st["banned"] and all(x["banned"] for x in st["findings"])
    call(base, t, "/api/ban", {"sites": ["127.0.0.1"], "remove": True})
    assert call(base, t, "/api/state")["banned"] == []


def test_site_lists_and_import(engine):
    a, base, _, data = engine
    t = a.token
    r = call(base, t, "/api/import", {"text": "# list from moz.com\nhttps://www.badsite.com/gallery\nother.org, # comment about skip.com\nnot a site", "into": "watch"})
    assert r["added"] == 2
    assert call(base, t, "/api/state")["watch"] == ["badsite.com", "other.org"]
    call(base, t, "/api/watch", {"sites": ["other.org"], "remove": True})
    assert call(base, t, "/api/state")["watch"] == ["badsite.com"]
    assert call(base, t, "/api/sister", {"name": "Radio group", "sites": ["a.com", "b.com"]})["ok"]
    groups = call(base, t, "/api/state")["sisters"]
    assert groups[0]["name"] == "Radio group" and groups[0]["named"]
    call(base, t, "/api/sister", {"name": "Radio group", "remove": True})
    assert call(base, t, "/api/state")["sisters"] == []
    # the comment lines at the top of the files survive
    assert (data / "watch.txt").read_text().startswith("# Sites to watch")


def test_fingerprint_job_from_the_app(engine, lib):  # noqa: F811
    a, base, _, _ = engine
    root = lib[0]
    assert call(base, a.token, "/api/index", {"folder": str(root)})["ok"]
    job = wait_job(base, a.token)
    assert job["state"] == "done" and job["result"]["skipped"] == 3
    assert not call(base, a.token, "/api/index", {"folder": str(root / "nope")})["ok"]


def test_window_closing_stops_the_engine(lib, monkeypatch):  # noqa: F811
    _, _, data = lib
    monkeypatch.setattr(appmod, "open_window", lambda url: None)
    monkeypatch.setattr(appmod, "BYE_GRACE_SECONDS", 0)
    started = {}

    real_init = App.__init__

    def spy(self, *a, **k):
        real_init(self, *a, **k)
        started["app"] = self

    monkeypatch.setattr(App, "__init__", spy)

    def bye_soon():
        for _ in range(50):
            if "app" in started:
                break
            time.sleep(0.05)
        started["app"].bye_at = time.time() + 0.01

    threading.Thread(target=bye_soon, daemon=True).start()
    t0 = time.time()
    appmod.serve(data, open_app=True)
    assert time.time() - t0 < 10


def test_crawler_sitemap_feed_robots(engine, tmp_path, lib):  # noqa: F811
    """Two sites: one with a sitemap (and a page robots.txt forbids), one with only an RSS feed."""
    a, base, _, data = engine
    root, files, _ = lib
    from test_pixelproof import _serve

    site = tmp_path / "crawl"
    def art(path, src):
        d = site / path
        d.mkdir(parents=True)
        Image.open(files[src]).resize((400, 300)).save(d / "p.jpg", quality=70)
        (d / "index.html").write_text("<img src='p.jpg'>")
    art("news/2026/maya-story", "MayaChen_2026-02-14_0031.jpg")
    art("private/secret-story", "LO 04-03-2026 shoot.png")
    (site / "index.html").write_text("<a href='/news/2026/maya-story/'>x</a>")
    srv = _serve(site)
    port = srv.server_port
    (site / "robots.txt").write_text(f"User-agent: *\nDisallow: /private/\nSitemap: http://127.0.0.1:{port}/sitemap.xml\n")
    (site / "sitemap.xml").write_text(
        "<urlset>" + "".join(f"<url><loc>http://127.0.0.1:{port}/{p}/</loc><lastmod>2026-11-01</lastmod></url>"
                             for p in ("news/2026/maya-story", "private/secret-story", "tag/photos")) + "</urlset>")
    try:
        call(base, a.token, "/api/watch", {"sites": ["127.0.0.1", "google.com"]})
        a.base_urls = {"127.0.0.1": f"http://127.0.0.1:{port}"}
        a.crawl_delay = 0
        assert call(base, a.token, "/api/crawl", {"pages": 10})["ok"]
        job = wait_job(base, a.token)
        assert job["state"] == "done", job["lines"]
        assert job["result"]["pages"] == 1 and len(job["result"]["new_ids"]) == 1   # private page skipped
        assert job["result"]["skipped"] == 1                                          # google.com
        st = call(base, a.token, "/api/state")
        crawl = {r["domain"]: r for r in st["crawl"]}
        assert crawl["127.0.0.1"]["checked"] == 1 and crawl["google.com"]["status"] == "skipped"
        assert any("robots" in ln for ln in job["lines"])
        # running again only looks at new pages
        call(base, a.token, "/api/crawl", {"pages": 10})
        job = wait_job(base, a.token)
        assert job["result"]["pages"] == 0
        assert "no new pages" in {r["domain"]: r for r in call(base, a.token, "/api/state")["crawl"]}["127.0.0.1"]["note"]
    finally:
        srv.shutdown()


def test_feed_parsing():
    from pixelproof.crawl import SiteCrawler

    class FakeSess:
        def __init__(self, pages):
            self.pages = pages

        def get(self, url, **k):
            class R:
                pass
            r = R()
            r.status_code = 200 if url in self.pages else 404
            r.content = self.pages.get(url, "").encode()
            r.url, r.encoding = url, "utf-8"
            return r

    rss = ("<rss><channel><item><link>https://site.com/2026/a-story</link><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate></item>"
           "<item><link>https://site.com/2026/older</link><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>"
           "<item><link>https://other.com/x/y</link></item></channel></rss>")
    sc = SiteCrawler("site.com", sess=FakeSess({"https://site.com/": "<html></html>", "https://site.com/feed": rss}), delay=0)
    assert sc.start()
    pages, how = sc.find_pages(10)
    assert how == "news feed" and pages == ["https://site.com/2026/a-story", "https://site.com/2026/older"]


def _gkg_zip(rows):
    import io
    import zipfile

    lines = []
    for site, url, img in rows:
        cols = [""] * 27
        cols[0], cols[1], cols[2], cols[3], cols[4], cols[18] = "1-1", "20261107031500", "1", site, url, img
        lines.append("\t".join(cols))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20261107031500.gkg.csv", "\n".join(lines))
    return buf.getvalue()


def test_news_feed_finds_new_sites(engine, monkeypatch):
    """A fake GDELT list: one US article uses our picture, one foreign one does too, one is unrelated."""
    import requests

    from pixelproof import newsfeed

    a, base, port, data = engine
    real_is_us = newsfeed.is_us
    monkeypatch.setattr(newsfeed, "is_us", lambda d: d == "127.0.0.1" or real_is_us(d))  # the test site
    web = f"http://127.0.0.1:{port}/ixp/993/p/anchor-article"
    rows = [("localpaper.com", f"{web}/", f"{web}/maya.jpg"),
            ("dawn.com", "https://www.dawn.com/news/1", f"{web}/luis.jpg"),
            ("other.com", "https://other.com/x", "https://other.com/none.jpg"),
            # the article page is gone but its picture is still there: the match is kept from the picture
            ("127.0.0.1", f"http://127.0.0.1:{port}/gone/", f"{web}/luis.jpg?v=2")]
    zipped = _gkg_zip(rows)
    real = requests.Session()

    class Sess:
        def get(self, url, **k):
            if url.startswith(newsfeed.GDELT):
                r = requests.Response()
                r.status_code, r._content = 200, zipped
                return r
            if "other.com" in url:
                raise requests.ConnectionError("no such site")
            return real.get(url, **k)

    a.news_session, a.news_stamps = Sess(), ["20261107031500"]
    assert call(base, a.token, "/api/news", {"hours": 1, "us_only": True})["ok"]
    job = wait_job(base, a.token)
    assert job["state"] == "done", job["lines"]
    res = job["result"]
    assert res["articles"] == 3 and res["matches"] == 2            # dawn.com left out (not US)
    assert res["new_sites"] == ["127.0.0.1"]                        # the article's real site
    st = call(base, a.token, "/api/state")
    assert "127.0.0.1" in st["watch"] and len(res["new_ids"]) == 3  # whole article (2) + gone page (1)
    gone = [f for f in st["findings"] if f["page_url"].endswith("/gone/")]
    assert gone and gone[0]["has_evidence"] and "news feed" in gone[0]["note"]
    # the same list is not read twice
    call(base, a.token, "/api/news", {"hours": 1})
    job = wait_job(base, a.token)
    assert job["result"]["articles"] == 0 and any("already checked" in ln for ln in job["lines"])


def test_us_only_filter():
    from pixelproof.newsfeed import is_us, parse_gkg, stamps_back

    assert is_us("nypost.com") and is_us("wtug.com") and is_us("kxbr.fm")
    assert not is_us("bbc.co.uk") and not is_us("aljazeera.com") and not is_us("www.thehindu.com")
    assert stamps_back("20261107031500", 1) == ["20261107031500", "20261107030000", "20261107024500", "20261107023000"]
    raw = b"header\tonly\n" + b"\t".join([b"x"] * 3 + [b"a.com", b"https://a.com/1"] + [b""] * 13 + [b"https://a.com/p.jpg"] + [b""] * 8)
    assert parse_gkg(raw) == [("a.com", "https://a.com/1", "https://a.com/p.jpg")]


def test_crawl_checks_stolen_sites_first(engine):
    a, base, port, data = engine
    url = f"http://127.0.0.1:{port}/ixp/993/p/anchor-article/"
    call(base, a.token, "/api/check", {"targets": [url]})
    job = wait_job(base, a.token)
    call(base, a.token, "/api/status", {"ids": job["result"]["new_ids"][:1], "status": "stolen"})
    stolen, found = a._infringers()
    assert stolen == {"127.0.0.1": 1} and found == {"127.0.0.1": 2}
    call(base, a.token, "/api/watch", {"sites": ["zzz.example", "aaa.example"]})
    a.base_urls = {"127.0.0.1": f"http://127.0.0.1:{port}", "zzz.example": "http://127.0.0.1:9", "aaa.example": "http://127.0.0.1:9"}
    a.crawl_delay = 0
    assert call(base, a.token, "/api/crawl", {"pages": 2})["ok"]          # 127.0.0.1 is not on the watch list
    job = wait_job(base, a.token)
    assert job["total"] == 3 and any("marked as stolen from first (127.0.0.1)" in ln for ln in job["lines"])


def test_old_lookalikes_are_rechecked(engine):
    """Matches from before the double-check that are a different photo get marked Not a match, once."""
    pytest.importorskip("cv2")
    import shutil

    from test_pixelproof import _detailed

    a, base, port, data = engine
    st = a.store
    ours = st.db.execute("SELECT id, path FROM images LIMIT 1").fetchone()
    _detailed(1).save(ours["path"])                  # make our file detailed enough to compare
    ev_same, ev_diff = data / "evidence" / "A", data / "evidence" / "B"
    ev_same.mkdir(parents=True), ev_diff.mkdir(parents=True)
    _detailed(1).crop((100, 80, 700, 480)).save(ev_same / "theirs.jpg")
    _detailed(7).save(ev_diff / "theirs.jpg")
    ids = []
    for page, ev in (("https://x.com/a", ev_same), ("https://x.com/b", ev_diff)):
        fid, _ = st.save_finding(page, page + ".jpg", "x.com", ours["id"], "possible", 0.9, 30, "similar")
        st.db.execute("UPDATE findings SET evidence_dir=? WHERE id=?", (str(ev), fid))
        ids.append(fid)
    st.set_meta("lookalike_recheck", "0")   # the app already did its one-time pass when it started
    st.commit()
    assert a.recheck_lookalikes() == 1
    assert st.finding(ids[0])["status"] == "new" and st.finding(ids[1])["status"] == "not_match"
    assert a.recheck_lookalikes() == 0
    shutil.rmtree(ev_same), shutil.rmtree(ev_diff)
