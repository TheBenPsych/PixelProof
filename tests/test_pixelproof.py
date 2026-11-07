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

    def device_label(self):
        return "test"

    def preprocess(self, img):
        a = np.asarray(img.convert("L").resize((16, 16)), dtype=np.float32).flatten()
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
    ImageOps.mirror(Image.open(files["IMG_5102.jpg"])).save(site / "flipped.jpg")  # flipped
    make_photo(99).save(site / "unrelated.jpg")                              # not ours
    make_photo(1, (40, 40)).save(site / "icon.png")                         # tiny, skipped
    (site / "page.html").write_text("""<html><body><h1>Stolen</h1>
        <img src="resized.jpg"><img src="flipped.jpg"><img src="unrelated.jpg">
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
    assert len(res.queries) == 3 and res.skipped_small == 1 and res.failed == 1
    by_name = {Path(q.source).name: q for q in res.queries}
    top = by_name["resized.jpg"].matches[0]
    assert top.category == "exact" and Path(top.row["path"]).name == "MayaChen_2026-02-14_0031.jpg"
    flip = by_name["flipped.jpg"].matches[0]
    assert flip.category == "edited" and flip.mirrored and Path(flip.row["path"]).name == "IMG_5102.jpg"
    assert top.match_id.startswith("PX-")
    report = write_report(res, data)
    text = report.read_text(encoding="utf-8")
    assert "Exact copy" in text and "Maya Chen" in text and "Open where found" in text

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
