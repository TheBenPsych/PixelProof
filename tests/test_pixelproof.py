import functools
import http.server
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter, ImageOps

from pixelproof import config, library
from pixelproof.check import check_target
from pixelproof.indexer import index_folder
from pixelproof.page import extract_image_urls, is_banned
from pixelproof.report import write_report
from pixelproof.store import Store


class FakeClip:
    """Stand-in for the AI model so tests run offline: a tiny blurred picture as the fingerprint."""
    dim = 256
    device = "cpu"
    name = "fake/v3"

    def device_label(self):
        return "test"

    def preprocess(self, img):
        a = np.asarray(img.convert("L").resize((16, 16)), dtype=np.float32)
        a = (a + a[:, ::-1]).flatten()   # like the real AI, a flipped picture looks the same
        a -= a.mean()
        return a

    def embed_tensors(self, tensors):
        v = np.stack(tensors).astype(np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)

    def embed(self, images):
        return self.embed_tensors([self.preprocess(i) for i in images])


def make_photo(seed, size=(800, 600)):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.BICUBIC)
    return img.filter(ImageFilter.GaussianBlur(8))


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photographers"
    files = {}
    layout = [("Maya Chen", "2026", "Q1", "MayaChen_2026-02-14_0031.jpg"),
              ("Maya Chen", "2025", "Q4", "IMG_5102.jpg"),
              ("Luis Ortega", "2026", "Q2", "LO 04-03-2026 shoot.png")]
    for seed, (who, year, q, name) in enumerate(layout):
        folder = root / who / year / q
        folder.mkdir(parents=True)
        path = folder / name
        make_photo(seed).save(path)
        files[name] = path
    (root / "Maya Chen" / "notes.txt").write_text("not an image")
    (root / "Maya Chen" / "2026" / "Q1" / "broken.jpg").write_bytes(b"not really a jpeg")
    data = tmp_path / "data"
    config.data_dir(data)
    return root, files, data


def test_describe_paths():
    info = library.describe(r"G:\My Drive\Photographers\Maya Chen\2026\Q1\MayaChen_2026-02-14_0031.jpg".replace("\\", "/"))
    assert (info.photographer, info.year, info.quarter, info.file_date) == ("Maya Chen", 2026, "Q1", "2026-02-14")
    info = library.describe("/x/Photographers/Luis/2024/3rd Quarter/LO 04-03-2024.png")
    assert (info.photographer, info.year, info.quarter, info.file_date) == ("Luis", 2024, "Q3", "2024-04-03")
    info = library.describe("/drive/Luis/random/IMG_20231231_1200.jpg", root="/drive")
    assert info.photographer == "Luis" and info.file_date == "2023-12-31" and info.year == 2023
    assert library.date_from_filename("IMG_5102.jpg") is None
    assert library.date_from_filename("11.1.26_Hochul, Mamdani_Yang.jpg") == "2026-11-01"
    assert library.date_from_filename("4.15.26_Kathy Hochul 4_Yang.jpg") == "2026-04-15"
    assert library.date_from_filename("v1.2.3.jpg") is None
    info = library.describe("C:/Users/Ben/Downloads/Yang 2026/2026/2nd quarter reg - 3.27.26-6.19.26/4.15.26_a.jpg")
    assert (info.photographer, info.year, info.quarter, info.file_date) == ("Yang", 2026, "Q2", "2026-04-15")
    assert library.date_from_filename("2026-13-40.jpg") is None


def test_index_resume_and_status(lib):
    root, files, data = lib
    store = Store(data)
    res = index_folder(str(root), store, FakeClip(), progress=False, log=lambda *a: None)
    assert (res.found, res.added, res.failed) == (4, 3, 1)
    assert store.count() == 3
    rows = {r["path"]: r for r in store.db.execute("SELECT * FROM images")}
    r = rows[str(files["MayaChen_2026-02-14_0031.jpg"])]
    assert (r["photographer"], r["year"], r["quarter"], r["file_date"]) == ("Maya Chen", 2026, "Q1", "2026-02-14")
    assert (r["width"], r["height"]) == (800, 600)
    # Second run skips everything already done.
    res2 = index_folder(str(root), store, FakeClip(), progress=False, log=lambda *a: None)
    assert res2.added == 0 and res2.skipped == 3


def test_extract_image_urls():
    html = """<html><head><meta property="og:image" content="/og.jpg"></head><body>
      <img src="a.jpg"><img data-src="//cdn.x.com/lazy.png" src="data:image/gif;base64,xx">
      <img srcset="s.jpg 300w, big.jpg 1200w"><a href="/full.webp?x=1">full</a>
      <div style="background-image:url('bg.jpg')"></div></body></html>"""
    urls = extract_image_urls(html, "https://site.com/p/page.html")
    for u in ["https://site.com/og.jpg", "https://site.com/p/a.jpg", "https://cdn.x.com/lazy.png",
              "https://site.com/p/big.jpg", "https://site.com/full.webp?x=1", "https://site.com/p/bg.jpg"]:
        assert u in urls
    assert not any(u.startswith("data:") for u in urls)


def test_banned():
    assert is_banned("https://www.wtug.com/x", {"wtug.com"})
    assert is_banned("https://news.wtug.com/x", {"wtug.com"})
    assert not is_banned("https://notwtug.com/x", {"wtug.com"})


def test_check_page_end_to_end(lib, tmp_path):
    root, files, data = lib
    store = Store(data)
    clip = FakeClip()
    index_folder(str(root), store, clip, progress=False, log=lambda *a: None)
    store.load_indexes(clip.dim)

    site = tmp_path / "site"
    site.mkdir()
    ours = Image.open(files["MayaChen_2026-02-14_0031.jpg"])
    ours.resize((400, 300)).save(site / "resized.jpg", quality=60)          # exact copy
    ours.resize((200, 150)).save(site / "resized-small.jpg", quality=60)    # same copy, another size
    ImageOps.mirror(Image.open(files["IMG_5102.jpg"])).save(site / "flipped.jpg")  # flipped
    make_photo(99).save(site / "unrelated.jpg")                              # not ours
    make_photo(1, (40, 40)).save(site / "icon.png")                         # tiny, skipped
    (site / "page.html").write_text("""<html><body><h1>Stolen</h1>
        <img src="resized-small.jpg"><img src="resized.jpg"><img src="flipped.jpg"><img src="unrelated.jpg">
        <img src="icon.png"><img src="missing.jpg"></body></html>""")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/page.html"
        res = check_target(url, store, clip, data, log=lambda *a: None)
    finally:
        server.shutdown()

    assert res.error == ""
    assert len(res.queries) == 4 and res.skipped_small == 1 and res.failed == 1
    # Two sizes of the same picture become one group, biggest copy first.
    assert len(res.groups) == 2
    g = res.groups[0]
    assert len(g.copies) == 2 and Path(g.main[0].source).name == "resized.jpg"
    by_name = {Path(q.source).name: q for q in res.queries}
    top = by_name["resized.jpg"].matches[0]
    assert top.category == "exact" and Path(top.row["path"]).name == "MayaChen_2026-02-14_0031.jpg"
    flip = by_name["flipped.jpg"].matches[0]
    assert flip.category == "edited" and flip.mirrored and Path(flip.row["path"]).name == "IMG_5102.jpg"
    assert top.match_id.startswith("PX-")
    report = write_report(res, data)
    text = report.read_text(encoding="utf-8")
    assert "Exact copy" in text and "1 other size" in text and "Maya Chen" in text and "Open where found" in text

    # Banned site is skipped.
    (data / "banned.txt").write_text("127.0.0.1\n")
    res = check_target(url, store, clip, data, log=lambda *a: None)
    assert res.banned


def test_check_local_file(lib):
    root, files, data = lib
    store = Store(data)
    clip = FakeClip()
    index_folder(str(root), store, clip, progress=False, log=lambda *a: None)
    store.load_indexes(clip.dim)
    res = check_target(str(files["LO 04-03-2026 shoot.png"]), store, clip, data, log=lambda *a: None)
    assert res.matched and res.matched[0].matches[0].category == "exact"
    write_report(res, data)


def test_cli_model_change_refingerprints(lib, monkeypatch, capsys):
    from pixelproof import cli

    root, files, data = lib
    store = Store(data)
    index_folder(str(root), store, FakeClip(), progress=False, log=lambda *a: None)
    store.commit()  # library made with the "old" model (no clip_model saved)
    monkeypatch.setattr(cli, "_clip", lambda d: FakeClip())
    assert cli.main(["--data", str(data), "check", str(files["IMG_5102.jpg"]), "--no-open"]) == 1
    assert "fresh fingerprints" in capsys.readouterr().out
    assert cli.main(["--data", str(data), "index", str(root)]) == 0
    assert "fresh fingerprint" in capsys.readouterr().out
    assert cli.main(["--data", str(data), "check", str(files["IMG_5102.jpg"]), "--no-open"]) == 0
    assert "1 of your images found" in capsys.readouterr().out


# ---------------------------------------------------------------- step 2

from pixelproof.sites import Approvals, SisterSites, add_approval, load_approvals, path_key  # noqa: E402
from pixelproof.summary import render_summary, write_summary  # noqa: E402


def test_path_key():
    a = path_key("https://catfishtuscaloosa.com/ixp/993/p/anchor-tuscaloosa-apps-2027-chamber/")
    b = path_key("https://wtug.com/ixp/993/p/anchor-tuscaloosa-apps-2027-chamber")
    assert a == b == "/ixp/993/p/anchor-tuscaloosa-apps-2027-chamber"
    assert path_key("https://wtug.com/") is None
    assert path_key("https://wtug.com/news/") is None          # too short to prove anything
    assert path_key("https://x.com/2026/index.html") is None
    assert path_key("C:/pics/a.jpg") is None


def test_sister_sites_and_named_company():
    pages = [("https://catfishtuscaloosa.com/ixp/993/p/a-long-article/", "catfishtuscaloosa.com", "/ixp/993/p/a-long-article"),
             ("https://wtug.com/ixp/993/p/a-long-article/", "wtug.com", "/ixp/993/p/a-long-article"),
             ("https://other.com/2026/11/unrelated-story", "other.com", "/2026/11/unrelated-story")]
    s = SisterSites([], pages)
    g = s.group_of("wtug.com")
    assert g and g.domains == ["catfishtuscaloosa.com", "wtug.com"] and not g.named
    assert s.group_of("news.wtug.com") is g
    assert s.group_of("other.com") is None
    assert "/ixp/993/p/a-long-article" in g.shared
    s = SisterSites([("Tuscaloosa Radio", ["wtug.com", "https://www.extra-site.com"])], pages)
    g = s.group_of("catfishtuscaloosa.com")
    assert g.name == "Tuscaloosa Radio" and g.named and "extra-site.com" in g.domains


def test_approvals_per_photographer(tmp_path):
    s = SisterSites([("Tuscaloosa Radio", ["wtug.com", "catfishtuscaloosa.com"])], [])
    a = Approvals([("Yang 2026", ["nypost.com"]), ("Maya Chen", ["Tuscaloosa Radio"])], s)
    assert a.licensed("Yang", "nypost.com") == "nypost.com"
    assert a.licensed("yang", "m.nypost.com") == "nypost.com"
    assert a.licensed("Luis", "nypost.com") is None            # only for that photographer
    assert a.licensed("Maya Chen", "wtug.com") == "Tuscaloosa Radio"
    assert a.licensed("Maya Chen", "nypost.com") is None
    config.data_dir(tmp_path)
    assert add_approval(tmp_path, "Yang", ["nypost.com"]) == ["nypost.com"]
    assert add_approval(tmp_path, "yang", ["nypost.com", "wtug.com"]) == ["wtug.com"]
    text = (tmp_path / "approved.txt").read_text()
    assert text.count("\nYang = nypost.com, wtug.com\n") == 1 and text.count("\nYang =") == 1
    assert load_approvals(tmp_path).licensed("Yang", "wtug.com") == "wtug.com"


def _serve(site):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_step2_end_to_end(lib, tmp_path, monkeypatch, capsys):
    """Two 'sites' (localhost and 127.0.0.1) carry the same article: sister sites, repeat copies, licensing."""
    root, files, data = lib
    store = Store(data)
    clip = FakeClip()
    index_folder(str(root), store, clip, progress=False, log=lambda *a: None)
    store.load_indexes(clip.dim)

    site = tmp_path / "site"
    art = site / "ixp" / "993" / "p" / "anchor-article"
    art.mkdir(parents=True)
    Image.open(files["MayaChen_2026-02-14_0031.jpg"]).resize((400, 300)).save(art / "maya.jpg", quality=70)
    Image.open(files["LO 04-03-2026 shoot.png"]).resize((400, 300)).save(art / "luis.jpg", quality=70)
    (art / "index.html").write_text("<img src='maya.jpg'><img src='luis.jpg'>")
    other = site / "2026" / "another-story"
    other.mkdir(parents=True)
    Image.open(files["MayaChen_2026-02-14_0031.jpg"]).resize((300, 225)).save(other / "m.jpg", quality=70)
    (other / "index.html").write_text("<img src='m.jpg'>")

    server = _serve(site)
    port = server.server_port
    a_url = f"http://127.0.0.1:{port}/ixp/993/p/anchor-article/"
    b_url = f"http://localhost:{port}/ixp/993/p/anchor-article/"
    c_url = f"http://127.0.0.1:{port}/2026/another-story/"
    try:
        add_approval(data, "Luis Ortega", ["localhost"])           # Luis licensed that site
        ra = check_target(a_url, store, clip, data, log=lambda *a: None)
        assert len(ra.stolen) == 2 and not ra.sister_group
        first_id = {g.row["photographer"]: g.match_id for g in ra.groups}

        rb = check_target(b_url, store, clip, data, log=lambda *a: None)
        assert rb.sister_group and set(rb.sister_group.domains) == {"127.0.0.1", "localhost"}
        assert [p["url"] for p in rb.sister_pages] == [a_url]
        luis = next(g for g in rb.groups if g.row["photographer"] == "Luis Ortega")
        maya = next(g for g in rb.groups if g.row["photographer"] == "Maya Chen")
        assert luis.licensed == "localhost" and not maya.licensed
        assert [f["page_url"] for f in maya.elsewhere] == [a_url]          # repeat copy
        text = write_report(rb, data).read_text(encoding="utf-8")
        assert "Sister site" in text and "Licensed for Luis Ortega" in text and "Repeat copy" in text

        # Checking the same page again keeps the same match ID.
        ra2 = check_target(a_url, store, clip, data, log=lambda *a: None)
        assert {g.row["photographer"]: g.match_id for g in ra2.groups} == first_id
        assert ra2.sister_group is not None                                  # now known as a sister site

        check_target(c_url, store, clip, data, log=lambda *a: None)
    finally:
        server.shutdown()

    html_text = render_summary(store, data)
    assert "Same photo on many pages" in html_text and "Every match, by site" in html_text
    assert "Sister sites: 127.0.0.1 + localhost" in html_text
    assert "Licensed uses (1)" in html_text and "Luis Ortega" in html_text
    assert store.count_pages() == 3
    assert write_summary(store, data).exists()

    # CLI: approve without questions.
    from pixelproof import cli

    assert cli.main(["--data", str(data), "approve", "maya chen", "https://www.nypost.com/x", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Approved for Maya Chen: nypost.com" in out
    assert cli.main(["--data", str(data), "summary", "--no-open"]) == 0


def test_old_step1_database_upgrades(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "library.db")
    db.executescript("""CREATE TABLE findings (id INTEGER PRIMARY KEY, found_at TEXT, page_url TEXT, image_url TEXT,
        domain TEXT, image_id INTEGER, category TEXT, clip_sim REAL, hash_dist INTEGER, note TEXT);
        INSERT INTO findings VALUES (1,'2026-11-06T20:00:00-05:00','https://nypost.com/a/b/c','https://i/x.jpg',
        'nypost.com',1,'exact',0.99,0,'same');""")
    db.commit()
    db.close()
    config.data_dir(tmp_path)
    store = Store(tmp_path)
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(findings)")}
    assert {"last_seen", "path_key", "thumb"} <= cols
    fid, found = store.save_finding("https://nypost.com/a/b/c", "https://i/x.jpg", "nypost.com", 1, "exact", 0.99, 0, "same")
    assert fid == 1 and found.startswith("2026-11-06")
    assert "Summary of all checks" in render_summary(store, tmp_path)


def _detailed(seed, size=(800, 533)):
    import random

    from PIL import ImageDraw

    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    d = ImageDraw.Draw(img)
    for _ in range(160):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        box = (x, y, x + rnd.randrange(10, 90), y + rnd.randrange(10, 90))
        fill = tuple(rnd.randrange(256) for _ in range(3))
        (d.rectangle if rnd.random() < 0.5 else d.ellipse)(box, fill=fill, outline=(0, 0, 0))
    return img


def test_double_check_tells_same_photo_from_lookalike(tmp_path):
    """The AI can say two photos of the same person 'look alike'. The double-check keeps edited copies of the
    same photo (crop, banner, colour, flip) and drops a different photo."""
    pytest.importorskip("cv2")
    from PIL import ImageDraw, ImageEnhance

    from pixelproof.matcher import double_check

    ours = _detailed(1)
    ours.save(tmp_path / "ours.jpg", quality=92)
    row = {"id": 1, "path": str(tmp_path / "ours.jpg")}
    crop = ours.crop((150, 100, 650, 433)).resize((420, 280))
    banner = ImageEnhance.Color(ours).enhance(1.7)
    ImageDraw.Draw(banner).rectangle((0, 470, 800, 533), fill=(200, 0, 0))
    for theirs, flipped in ((crop, False), (banner, False), (ImageOps.mirror(ours), True)):
        cat, note = double_check(theirs, row, flipped, "possible", "similar, needs a human look", {})
        assert cat == "edited" and "details line up" in note
    assert double_check(_detailed(2), row, False, "possible", "x", {}) == (None, "")
    # our file moved: the hit is kept, with a note
    cat, note = double_check(crop, {"id": 2, "path": str(tmp_path / "gone.jpg")}, False, "possible", "x", {})
    assert cat == "possible" and "could not be double-checked" in note


def test_double_check_ignores_a_shared_flag():
    """Two different photos that both show the same flag (stripes and stars line up by chance) are not a match."""
    pytest.importorskip("cv2")
    from PIL import ImageDraw

    from pixelproof.fingerprint import same_photo_score

    def flag():
        f = Image.new("RGB", (190, 100), "white")
        d = ImageDraw.Draw(f)
        for i in range(0, 13, 2):
            d.rectangle((0, i * 100 // 13, 190, (i + 1) * 100 // 13), fill=(178, 34, 52))
        d.rectangle((0, 0, 76, 54), fill=(60, 59, 110))
        for y in range(5, 54, 10):
            for x in range(6, 76, 12):
                d.ellipse((x, y, x + 4, y + 4), fill="white")
        return f

    a, b = _detailed(11), _detailed(12)
    a.paste(flag(), (60, 60))
    b.paste(flag().resize((285, 150)), (400, 300))
    assert same_photo_score(a, b) == 0
    assert same_photo_score(a.crop((40, 40, 700, 500)), a) > 20


def test_search_on_card_gives_same_results(lib):
    """The graphics-card search (tested here on the processor) finds the same images as the normal search."""
    pytest.importorskip("torch")
    root, files, data = lib
    store = Store(data)
    clip = FakeClip()
    index_folder(str(root), store, clip, progress=False, log=lambda *a: None)
    store.load_indexes(clip.dim)
    q = clip.embed([Image.open(p) for p in list(files.values())[:3]])
    s1, i1 = store.search_clip(q, 3)
    assert store.search_on_card("cpu")
    s2, i2 = store.search_clip(q, 3)
    assert (i1 == i2).all() and np.allclose(s1, s2, atol=1e-4)
