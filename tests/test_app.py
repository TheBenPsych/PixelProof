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
