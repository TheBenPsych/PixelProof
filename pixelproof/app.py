"""The PixelProof desktop app.

It runs a small private web server on this PC only (127.0.0.1, never reachable from other
computers) and opens it in an app window (Microsoft Edge "app mode", no address bar).
Close the window and PixelProof stops by itself a few seconds later.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config

UI_DIR = Path(__file__).resolve().parent / "ui"
WATCH_FILE = "watch.txt"
WATCH_TEMPLATE = """\
# Sites to watch: sites known for using your images without permission.
# The crawler (a later step) will visit these. One site per line. Lines starting with # are ignored.
"""
IDLE_EXIT_SECONDS = 180  # no sign of the app window for this long -> quit (unless a job is running)
BYE_GRACE_SECONDS = 6    # the window said goodbye (closed): quit after this, unless it comes back (a reload)


# ------------------------------------------------------------------ site list files

def _read_sites(path: Path) -> list[str]:
    from .sites import clean_site

    out = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            site = clean_site(line) if line else ""
            if site and site not in out:
                out.append(site)
    except FileNotFoundError:
        pass
    return out


def _write_sites(path: Path, sites: list[str], header: str) -> None:
    keep = []
    try:  # keep the comment lines at the top
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("#") or not line.strip():
                keep.append(line)
            else:
                break
    except FileNotFoundError:
        keep = header.rstrip("\n").splitlines()
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(keep + sites) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _rewrite_rules(path: Path, header: str, rules: list[tuple[str, list[str]]]) -> None:
    keep = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("#") or not line.strip():
                keep.append(line)
            else:
                break
    except FileNotFoundError:
        keep = header.rstrip("\n").splitlines()
    body = [f"{name} = {', '.join(items)}" for name, items in rules if items]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(keep + body) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _too_big():
    from .crawl import TOO_BIG

    return TOO_BIG


# ------------------------------------------------------------------ "Browse..." choosers

def _fix_tcl_paths() -> None:
    """Python's private space (.venv) sometimes can't find the files the chooser needs (Tcl/Tk).
    Point it at the main Python install, where they are."""
    import glob

    base = Path(sys.base_prefix)
    for var, pattern in (("TCL_LIBRARY", "tcl*"), ("TK_LIBRARY", "tk*")):
        if os.environ.get(var):
            continue
        for folder in (base / "tcl", base / "lib", base / "Lib"):
            hits = sorted(glob.glob(str(folder / pattern)), reverse=True)
            hits = [h for h in hits if os.path.isfile(os.path.join(h, "init.tcl" if var == "TCL_LIBRARY" else "tk.tcl"))]
            if hits:
                os.environ[var] = hits[0]
                break


def _powershell_pick(kind: str) -> str | None:
    """Opens the Windows chooser with PowerShell. Returns the path, "" if cancelled, None if it failed."""
    if kind == "folder":
        script = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$d.Description='Choose a folder of your images';$d.ShowNewFolderButton=$false;"
                  "$f=New-Object System.Windows.Forms.Form;$f.TopMost=$true;"
                  "if($d.ShowDialog($f) -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}")
    else:
        flt = "Text files (*.txt)|*.txt|All files (*.*)|*.*" if kind == "list" else \
            "Pictures|" + ";".join("*" + e for e in sorted(config.IMAGE_EXTENSIONS)) + "|All files (*.*)|*.*"
        script = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                  f"$d.Filter='{flt}';"
                  "$f=New-Object System.Windows.Forms.Form;$f.TopMost=$true;"
                  "if($d.ShowDialog($f) -eq 'OK'){[Console]::Out.Write($d.FileName)}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                             capture_output=True, text=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


# ------------------------------------------------------------------ background jobs

class Job:
    def __init__(self, kind: str, label: str):
        self.kind, self.label = kind, label
        self.state = "running"           # running | done | error | stopped
        self.lines: list[str] = []
        self.all_lines: list[str] = []
        self.done = self.total = self.found = 0
        self.result: dict = {}
        self.stop = False
        self.started = time.time()

    def log(self, *parts):
        text = " ".join(str(p) for p in parts).strip()
        if text:
            t = int(time.time() - self.started)
            text = f"[{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}] {text}" if t >= 3600 else f"[{t // 60:02d}:{t % 60:02d}] {text}"
            self.lines.append(text)
            del self.lines[:-200]
            self.all_lines.append(text)
            del self.all_lines[:-20000]

    def text(self) -> str:
        """The whole log, for the Copy button."""
        head = [f"PixelProof {config.VERSION} · {self.kind}: {self.label}",
                f"Started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started))}, "
                f"{self.state}, {round(time.time() - self.started)} seconds so far", ""]
        return "\n".join(head + self.all_lines) + "\n"

    def as_dict(self):
        return {"kind": self.kind, "label": self.label, "state": self.state, "lines": self.lines[-60:],
                "done": self.done, "total": self.total, "found": self.found, "result": self.result,
                "seconds": round(time.time() - self.started)}


class App:
    def __init__(self, data_dir: Path, clip_factory=None):
        from .store import Store

        self.data_dir = Path(data_dir)
        (self.data_dir / "proofs").mkdir(exist_ok=True)
        (self.data_dir / "evidence").mkdir(exist_ok=True)
        (self.data_dir / "cache").mkdir(exist_ok=True)
        if not (self.data_dir / WATCH_FILE).exists():
            (self.data_dir / WATCH_FILE).write_text(WATCH_TEMPLATE, encoding="utf-8")
        self.store = Store(self.data_dir)
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(18)
        self.job: Job | None = None
        self.clip = None
        self.clip_factory = clip_factory
        self.clip_lock = threading.Lock()
        self.last_seen = time.time()
        self.bye_at = 0.0
        self.base_urls: dict[str, str] = {}   # tests: domain -> http://host:port
        self.crawl_delay = 1.0                # seconds between pages on one site (be polite)
        try:
            self.recheck_lookalikes()
        except Exception:
            traceback.print_exc()
        self.news_session = None              # tests: a fake web session for the news feed
        self.news_stamps = None               # tests: which 15-minute news lists to read

    # ---------- AI model (loaded once, the first time it is needed) ----------
    def get_clip(self, job: Job):
        with self.clip_lock:
            if self.clip is None:
                job.log("Loading the AI fingerprint model (takes a few seconds) ...")
                if self.clip_factory:
                    self.clip = self.clip_factory(self.data_dir)
                else:
                    from .cli import _clip

                    self.clip = _clip(self.data_dir)
                job.log(f"Using your {self.clip.device_label()}.")
            return self.clip

    # ---------- what the app shows ----------
    def state(self) -> dict:
        from .matcher import CATEGORY_LABELS
        from .page import is_banned, load_banned
        from .sites import load_approvals, load_sisters
        from .store import match_id

        with self.lock:
            st = self.store
            banned = sorted(load_banned(self.data_dir / "banned.txt"))
            sisters = load_sisters(self.data_dir, st)
            approvals = load_approvals(self.data_dir, sisters)
            people = [dict(r) for r in st.db.execute(
                "SELECT COALESCE(photographer,'Unknown') AS name, COUNT(*) AS images, MIN(year) AS y1, MAX(year) AS y2 "
                "FROM images WHERE clip IS NOT NULL GROUP BY name ORDER BY name COLLATE NOCASE")]
            periods = {}
            for r in st.db.execute(
                    "SELECT COALESCE(photographer,'Unknown') p, year, quarter, COUNT(*) n FROM images "
                    "WHERE clip IS NOT NULL GROUP BY p, year, quarter"):
                periods.setdefault(r[0], []).append({"year": r[1], "quarter": r[2], "n": r[3]})
            for p in people:
                p["periods"] = sorted(periods.get(p["name"], []), key=lambda x: (-(x["year"] or 0), x["quarter"] or ""))

            finds = []
            for r in st.db.execute(
                    "SELECT f.id, f.found_at, f.last_seen, f.times_seen, f.page_url, f.image_url, f.domain, f.image_id, "
                    "f.category, f.clip_sim, f.hash_dist, f.note, f.path_key, f.width, f.height, f.status, f.status_at, "
                    "f.evidence_dir, f.proof_path, f.thumb IS NOT NULL AS has_thumb, i.path AS our_path, i.photographer, "
                    "i.year, i.quarter, i.file_date, i.width AS our_w, i.height AS our_h "
                    "FROM findings f LEFT JOIN images i ON i.id=f.image_id ORDER BY f.id"):
                d = dict(r)
                d["match_id"] = match_id(d["id"], d["found_at"])
                d["category_label"] = CATEGORY_LABELS.get(d["category"], d["category"])
                d["status"] = d["status"] or "new"
                is_page = bool(d["page_url"])
                d["banned"] = bool(is_page and is_banned(d["page_url"], set(banned)))
                d["licensed"] = approvals.licensed(d["photographer"], d["domain"]) if is_page else None
                g = sisters.group_of(d["domain"]) if is_page else None
                d["company"] = g.name if g else None
                d["sister_sites"] = g.domains if g else []
                d["photographer"] = d["photographer"] or "Unknown"
                d["file_name"] = os.path.basename(d["our_path"] or "")
                d["has_evidence"] = bool(d.pop("evidence_dir"))
                d["has_proof"] = bool(d["proof_path"] and os.path.exists(d["proof_path"]))
                finds.append(d)

            approved = [{"photographer": who, "sites": items}
                        for who, items in self._approval_rules()]
            groups = [{"name": g.name, "named": g.named, "sites": g.domains, "shared": len(g.shared)}
                      for g in sisters.groups]
            return {
                "version": config.VERSION,
                "token": self.token,
                "library": {"images": st.count(), "photographers": people,
                            "errors": st.db.execute("SELECT COUNT(*) FROM errors").fetchone()[0],
                            "pages": st.count_pages()},
                "findings": finds,
                "banned": banned,
                "approved": approved,
                "sisters": groups,
                "watch": _read_sites(self.data_dir / WATCH_FILE),
                "crawl": [dict(r) for r in st.db.execute("SELECT * FROM crawl_sites")],
                "too_big": sorted(_too_big()),
                "job": self.job.as_dict() if self.job else None,
                "device": self.clip.device_label() if self.clip else None,
                "data_dir": str(self.data_dir),
            }

    def _approval_rules(self):
        from .sites import APPROVED_FILE, _read_rules

        return _read_rules(self.data_dir / APPROVED_FILE)

    # ---------- actions ----------
    def start_job(self, kind: str, label: str, work) -> dict:
        with self.lock:
            if self.job and self.job.state == "running":
                return {"ok": False, "error": "PixelProof is still busy with: " + self.job.label}
            job = self.job = Job(kind, label)

        def run():
            try:
                work(job)
                if job.state == "running":
                    job.state = "stopped" if job.stop else "done"
            except Exception as e:
                job.state = "error"
                job.log(f"Something went wrong: {type(e).__name__}: {e}")
                traceback.print_exc()

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def check(self, targets: list[str]) -> dict:
        from .check import check_target
        from .cli import _library_model
        from .store import Store

        targets = [t.strip().strip('"') for t in targets if t and t.strip()]
        if not targets:
            return {"ok": False, "error": "Paste a page link first."}
        with self.lock:
            if self.store.count() == 0:
                return {"ok": False, "error": "Your library is empty. Add a folder of your images first (My images)."}

        from .page import domain_of

        one = targets[0]
        label = (domain_of(one) or one) if one.lower().startswith("http") else os.path.basename(one)
        if len(targets) > 1:
            label = f"{len(targets)} links"

        def work(job: Job):
            clip = self.get_clip(job)
            store = Store(self.data_dir)  # this job's own connection
            if _library_model(store) != clip.name:
                job.state = "error"
                job.log("Your library needs fresh fingerprints first. Open My images and add your folder again.")
                return
            store.load_indexes(clip.dim)
            job.total = len(targets)
            new_ids, all_ids, checked = [], [], 0
            for t in targets:
                if job.stop:
                    break
                job.log(f"Checking {t}")
                before = store.db.execute("SELECT COALESCE(MAX(id),0) FROM findings").fetchone()[0]
                res = check_target(t, store, clip, self.data_dir, log=job.log)
                checked += 1
                job.done = checked
                if res.error or res.banned:
                    if len(targets) == 1:
                        job.state = "error" if res.error else "done"
                        job.result = {"error": res.error, "banned": res.banned, "domain": res.domain}
                    continue
                ids = [g.finding_id for g in res.groups]
                all_ids += ids
                new_ids += [i for i in ids if i > before]
                job.log(f"{len(res.queries)} pictures checked, {len(res.groups)} of your images found"
                        + (f" ({len(res.licensed)} licensed)" if res.licensed else "") + ".")
            job.found = len(new_ids)
            job.result.update({"new_ids": new_ids, "ids": all_ids, "checked": checked})
            store.db.close()

        return self.start_job("check", label, work)

    def index(self, folder: str) -> dict:
        from .cli import _library_model
        from .indexer import index_folder
        from .store import Store

        folder = (folder or "").strip().strip('"')
        if not folder or not os.path.isdir(folder):
            return {"ok": False, "error": "That folder was not found. Use Browse to pick it."}

        def work(job: Job):
            clip = self.get_clip(job)
            store = Store(self.data_dir)
            old = _library_model(store)
            if old and old != clip.name:
                job.log("PixelProof has a better AI model setting now, so every image gets a fresh fingerprint.")
                store.db.execute("UPDATE images SET clip=NULL")
                store.mark_dirty()
            store.set_meta("clip_model", clip.name)
            store.commit()

            def prog(done, total, found):
                job.done, job.total, job.found = done, total, found

            res = index_folder(folder, store, clip, log=job.log, progress=False, on_progress=prog,
                               should_stop=lambda: job.stop)
            job.result = {"found": res.found, "added": res.added, "skipped": res.skipped, "failed": res.failed,
                          "seconds": round(res.seconds), "stopped": res.stopped}
            job.log(f"Done. New or changed images: {res.added:,}. Already done before: {res.skipped:,}."
                    + (f" Could not read: {res.failed:,}." if res.failed else ""))
            if res.stopped:
                job.state = "stopped"
            store.db.close()

        return self.start_job("index", folder, work)

    def crawl(self, sites: list[str] | None, pages_per_site: int = 20, workers: int = 12) -> dict:
        """Visits the watch-list sites (or just `sites`) and checks their newest pages."""
        from concurrent.futures import ThreadPoolExecutor

        from .cli import _library_model
        from .crawl import TOO_BIG, crawl_site, save_site
        from .store import Store

        with self.lock:
            if self.store.count() == 0:
                return {"ok": False, "error": "Your library is empty. Add a folder of your images first (My images)."}
            watch = _read_sites(self.data_dir / WATCH_FILE)
            done = {r[0]: r[1] or "" for r in self.store.db.execute("SELECT domain, last_crawl FROM crawl_sites")}
            stolen, found = self._infringers()
        targets = [s for s in (sites or watch) if s]
        if not sites:
            # sites you marked as stolen from go first, even if they are not on the watch list
            targets += [d for d in stolen if d not in targets]
        if not targets:
            return {"ok": False, "error": "Your watch list is empty. Add sites in Site lists first."}
        if not sites:
            # 1) most "stolen" matches  2) any match found before  3) never crawled  4) crawled longest ago
            targets.sort(key=lambda d: (-stolen.get(d, 0), -found.get(d, 0), done.get(d, "")))
        first = [d for d in targets if d in stolen]
        pages_per_site = max(1, min(int(pages_per_site or 20), 500))
        workers = max(1, min(int(workers or 12), 64))
        factory = self.clip_factory

        def work(job: Job):
            clip = self.get_clip(job)
            main = Store(self.data_dir)
            if _library_model(main) != clip.name:
                job.state = "error"
                job.log("Your library needs fresh fingerprints first. Open My images and add your folder again.")
                return
            main.load_indexes(clip.dim)
            lock = threading.Lock()

            class SharedClip:  # one AI model on the graphics card, used by one site at a time
                dim, name, device = clip.dim, clip.name, getattr(clip, "device", "")
                preprocess = staticmethod(clip.preprocess)

                def embed(self, images):
                    # Preparing the pictures (resizing etc.) runs at the same time for every site; only the
                    # graphics card step itself is one at a time. (Before 0.5.8 everything waited in line.)
                    tensors = [clip.preprocess(im) for im in images]
                    with lock:
                        return clip.embed_tensors(tensors)

                def embed_tensors(self, tensors):
                    with lock:
                        return clip.embed_tensors(tensors)

                def device_label(self):
                    return clip.device_label()

            shared = SharedClip()
            seen: set = set()
            job.total = len(targets)
            job.result = {"new_ids": [], "sites": 0, "pages": 0, "matches": 0, "skipped": 0}
            if first and not sites:
                job.log(f"Checking the {len(first)} site{'s' if len(first) != 1 else ''} you marked as stolen from "
                        f"first ({', '.join(first[:3])}{' ...' if len(first) > 3 else ''}).")
            big = [d for d in targets if d in TOO_BIG]
            if big:
                job.log(f"Skipping {len(big)} giant platforms that can't be crawled (like {', '.join(big[:3])}).")

            timings = []   # (seconds, site, pages checked)
            sums = {"t_page": 0.0, "t_pictures": 0.0, "t_compare": 0.0, "pictures": 0}
            job.log(f"Crawling {len(targets)} sites, {workers} at once, up to {pages_per_site} new pages each.")

            def one(domain):
                if job.stop:
                    return
                t0 = time.time()
                st = Store(self.data_dir)
                st._clip_index, st._hash_index = main._clip_index, main._hash_index
                counted = {"pages": 0, "matches": 0, "ids": 0}

                def tally(r):  # adds this site's progress to the totals as each page finishes
                    with lock:
                        res = job.result
                        res["pages"] += r.checked - counted["pages"]
                        res["matches"] += r.matches - counted["matches"]
                        res["new_ids"] += r.new_ids[counted["ids"]:]
                        counted.update(pages=r.checked, matches=r.matches, ids=len(r.new_ids))
                        job.found = len(res["new_ids"])

                try:
                    r = crawl_site(domain, st, shared, self.data_dir, pages_per_site=pages_per_site,
                                   log=job.log, should_stop=lambda: job.stop, seen_images=seen,
                                   base_url=self.base_urls.get(domain), delay=self.crawl_delay,
                                   on_page=lambda i, n, r: tally(r))
                except Exception as e:  # one broken site never stops the run
                    from .crawl import SiteResult

                    r = SiteResult(domain, "error", f"{type(e).__name__}: {e}"[:200])
                save_site(st, r)
                st.db.close()
                tally(r)
                with lock:
                    job.done += 1
                    job.result["sites"] += 1
                    job.result["skipped"] += r.status == "skipped"
                took = time.time() - t0
                with lock:
                    timings.append((took, domain, r.checked))
                per = f", {took / r.checked:.1f}s per page" if r.checked else ""
                took_s = f"{took:.1f}s" if took < 10 else f"{took:.0f}s"
                if r.status == "error":
                    job.log(f"{domain}: {r.note} (after {took_s})")
                elif r.status == "done":
                    split = (f" Per page: {r.pictures / r.checked:.0f} pictures, opening {r.t_page / r.checked:.1f}s, "
                             f"downloading pictures {r.t_pictures / r.checked:.1f}s, comparing {r.t_compare / r.checked:.1f}s, "
                             f"other (finding pages, polite pause) {max(0.0, took - r.t_page - r.t_pictures - r.t_compare) / r.checked:.1f}s."
                             if r.checked else "")
                    job.log(f"{domain}: done in {took_s}. {r.checked} page{'s' if r.checked != 1 else ''} checked"
                            f"{per}, {r.matches} of your images found.{split}")
                    with lock:
                        for k in ("t_page", "t_pictures", "t_compare"):
                            sums[k] += getattr(r, k)
                        sums["pictures"] += r.pictures

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(one, targets))
            res = job.result
            total = max(time.time() - job.started, 1)
            job.log(f"Finished: {res['sites']} sites, {res['pages']} pages checked, "
                    f"{len(res['new_ids'])} new matches.")
            worked = [t for t in timings if t[2]]
            took_all = f"{total:.0f} second{'s' if round(total) != 1 else ''}" if total < 120 else f"{total / 60:.1f} minutes"
            job.log(f"Speed: {took_all} in all, {workers} sites at once, "
                    f"{res['sites'] / total * 60:.1f} sites per minute, {res['pages'] / total * 60:.1f} pages per minute"
                    + (f", {sum(t[0] for t in worked) / len(worked):.0f}s per site that had pages." if worked else "."))
            busy = sum(sums[k] for k in ("t_page", "t_pictures", "t_compare")) or 1
            if res["pages"]:
                job.log(f"Where the time went: opening pages {sums['t_page'] / busy:.0%}, downloading pictures "
                        f"{sums['t_pictures'] / busy:.0%}, comparing pictures {sums['t_compare'] / busy:.0%}. "
                        f"{sums['pictures'] / res['pages']:.0f} pictures per page on average.")
            slow = sorted(worked, reverse=True)[:5]
            if slow and len(worked) > 5:
                job.log("Slowest sites: " + ", ".join(f"{d} {t:.0f}s ({n} pages)" for t, d, n in slow))
            main.db.close()

        label = targets[0] if len(targets) == 1 else f"{len(targets)} sites"
        return self.start_job("crawl", label, work)

    def recheck_lookalikes(self) -> int:
        """Once, after the update that added the double-check: matches still waiting for review that only
        "looked alike" are checked again, and different photos are marked "Not a match"."""
        import glob

        from .fingerprint import open_image
        from .matcher import double_check

        st = self.store
        if st.get_meta("lookalike_recheck_3") == "1":
            return 0
        try:
            import cv2  # noqa: F401  (the double-checker)
        except Exception:
            return 0
        dropped = 0
        rows = st.db.execute(
            "SELECT f.id, f.evidence_dir, f.note, i.id AS iid, i.path FROM findings f JOIN images i ON i.id=f.image_id "
            "WHERE COALESCE(f.status,'new')='new' AND (f.hash_dist>? OR f.clip_sim<?) AND f.evidence_dir IS NOT NULL",
            (config.EXACT_MAX_HASH_DIST, config.HASH_HIT_MIN_CLIP)).fetchall()
        cache = {}
        for r in rows:
            theirs = glob.glob(os.path.join(r["evidence_dir"], "theirs.*"))
            if not theirs:
                continue
            try:
                img = open_image(theirs[0])[0]
            except Exception:
                continue
            flipped = "flipped" in (r["note"] or "")
            cat, _ = double_check(img, {"id": r["iid"], "path": r["path"]}, flipped, "possible", "", cache)
            if cat is None:
                st.set_status([r["id"]], "not_match")
                st.db.execute("UPDATE findings SET note=? WHERE id=?",
                              ((r["note"] or "") + "; different photo (double-check)", r["id"]))
                dropped += 1
        if cache and all(v is None for v in cache.values()):
            return 0   # our files are not reachable right now (drive unplugged?): try again next start
        st.set_meta("lookalike_recheck_3", "1")
        st.commit()
        return dropped

    def _infringers(self) -> tuple[dict, dict]:
        """Sites with matches: {site: number marked stolen}, {site: number of matches}. Banned sites are left out."""
        from .page import load_banned

        banned = load_banned(self.data_dir / "banned.txt")
        stolen, found = {}, {}
        for dom, status, n in self.store.db.execute(
                "SELECT domain, COALESCE(status,'new'), COUNT(*) FROM findings "
                "WHERE domain IS NOT NULL AND domain<>'' GROUP BY domain, status"):
            if dom in banned or any(dom.endswith("." + b) for b in banned):
                continue
            if status == "stolen":
                stolen[dom] = stolen.get(dom, 0) + n
            if status != "not_match":
                found[dom] = found.get(dom, 0) + n
        return stolen, found

    def news(self, hours: float = 3, us_only: bool = True) -> dict:
        """Reads the free GDELT news feed and checks every article's main picture against the library."""
        from . import newsfeed
        from .cli import _library_model
        from .store import Store

        with self.lock:
            if self.store.count() == 0:
                return {"ok": False, "error": "Your library is empty. Add a folder of your images first (My images)."}
        hours = max(0.25, min(float(hours or 3), 72))

        def work(job: Job):
            clip = self.get_clip(job)
            store = Store(self.data_dir)
            if _library_model(store) != clip.name:
                job.state = "error"
                job.log("Your library needs fresh fingerprints first. Open My images and add your folder again.")
                return
            store.load_indexes(clip.dim)
            watch = set(_read_sites(self.data_dir / WATCH_FILE))

            from .sites import is_unlikely

            def add_watch(site):
                if site in watch or is_unlikely(site):
                    return False
                watch.add(site)
                self.watch([site])
                return True

            job.result = {"new_ids": [], "articles": 0, "pictures": 0, "matches": 0, "new_sites": []}

            def progress(i, n, res):
                job.done, job.total = i - 1, n
                job.result.update({k: res[k] for k in ("new_ids", "articles", "pictures", "matches", "new_sites")})
                job.found = len(res["new_ids"])

            job.log(f"Reading the news feed for the last {hours:g} hour{'s' if hours != 1 else ''}"
                    + (" (US sites only)" if us_only else "") + " ...")
            res = newsfeed.scan(store, clip, self.data_dir, hours=hours, us_only=us_only, log=job.log,
                                should_stop=lambda: job.stop, on_progress=progress, add_watch=add_watch,
                                sess=self.news_session, stamps=self.news_stamps)
            progress(res["lists"] + 1, max(res["lists"], 1), res)
            job.done = job.total
            job.result.update(res)
            job.log(f"Finished: {res['articles']} news articles checked, {len(res['new_ids'])} new matches, "
                    f"{len(res['new_sites'])} new site{'s' if len(res['new_sites']) != 1 else ''} added to your watch list.")
            store.db.close()

        return self.start_job("news", f"News feed, last {hours:g} h", work)

    def set_status(self, ids, status) -> dict:
        with self.lock:
            n = self.store.set_status(ids, status)
        return {"ok": True, "changed": n}

    def ban(self, sites: list[str], unban=False) -> dict:
        from .sites import clean_site

        path = self.data_dir / "banned.txt"
        with self.lock:
            from .page import load_banned

            have = sorted(load_banned(path))
            sites = [clean_site(s) for s in sites if clean_site(s)]
            if unban:
                have = [h for h in have if h not in sites]
            else:
                have += [s for s in sites if s not in have]
            _write_sites(path, have, "")
        return {"ok": True}

    def approve(self, photographer: str, sites: list[str], remove=False) -> dict:
        from .sites import APPROVED_FILE, APPROVED_TEMPLATE, _person_key, add_approval, clean_site

        with self.lock:
            if not remove:
                cleaned = [clean_site(s) or s.strip() for s in sites if s.strip()]
                add_approval(self.data_dir, photographer, cleaned)
            else:
                rules = []
                drop = {s.lower() for s in sites}
                for who, items in self._approval_rules():
                    if _person_key(who) == _person_key(photographer):
                        items = [i for i in items if i.lower() not in drop]
                    rules.append((who, items))
                _rewrite_rules(self.data_dir / APPROVED_FILE, APPROVED_TEMPLATE, rules)
        return {"ok": True}

    def sister(self, name: str, sites: list[str], remove=False) -> dict:
        from .sites import SISTERS_FILE, SISTERS_TEMPLATE, _read_rules, clean_site

        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Give the company a name."}
        with self.lock:
            rules = _read_rules(self.data_dir / SISTERS_FILE)
            rules = [(n, i) for n, i in rules if n.lower() != name.lower()]
            if not remove:
                cleaned = list(dict.fromkeys(clean_site(s) for s in sites if clean_site(s)))
                if len(cleaned) < 2:
                    return {"ok": False, "error": "List at least two sites."}
                rules.append((name, cleaned))
            _rewrite_rules(self.data_dir / SISTERS_FILE, SISTERS_TEMPLATE, rules)
        return {"ok": True}

    def watch(self, sites: list[str], remove=False) -> dict:
        from .sites import clean_site

        path = self.data_dir / WATCH_FILE
        with self.lock:
            have = _read_sites(path)
            sites = [clean_site(s) for s in sites if clean_site(s)]
            have = [h for h in have if h not in sites] if remove else have + [s for s in sites if s not in have]
            _write_sites(path, have, WATCH_TEMPLATE)
        return {"ok": True}

    def unlikely(self, apply: bool = False) -> dict:
        """Sites on the watch list that will practically never have your photos (domain sellers, software,
        science, shops, government, giant platforms). Sites where you already have matches are kept.
        With apply=True they are removed, and saved in removed-from-watch.txt so they can be imported back."""
        from .sites import is_unlikely

        path = self.data_dir / WATCH_FILE
        with self.lock:
            have = _read_sites(path)
            stolen, found = self._infringers()
            drop = [s for s in have if is_unlikely(s) and s not in found and s not in stolen]
            if apply and drop:
                _write_sites(path, [s for s in have if s not in drop], WATCH_TEMPLATE)
                with open(self.data_dir / "removed-from-watch.txt", "a", encoding="utf-8") as f:
                    f.write(f"# Removed by 'Remove unlikely sites' on {time.strftime('%Y-%m-%d %H:%M')}. "
                            "Import this file to add them back.\n" + "\n".join(drop) + "\n")
        return {"ok": True, "sites": drop, "keep": len(have) - len(drop), "removed": bool(apply and drop)}

    def import_sites(self, text: str, into: str) -> dict:
        from .sites import clean_site

        sites = []
        for row in (text or "").splitlines():
            row = row.split("#", 1)[0]          # skip comments, even ones that name a site
            for item in re.split(r"[,;\t ]+", row):
                item = item.strip()
                site = clean_site(item) if "." in item else ""
                if site and "." in site and site not in sites:
                    sites.append(site)
        if not sites:
            return {"ok": False, "error": "No sites found in that text."}
        (self.ban if into == "ban" else self.watch)(sites)
        return {"ok": True, "added": len(sites), "sites": sites}

    def proof(self, finding_id: int) -> dict:
        from .proof import build_pdf
        from .sites import load_approvals, load_sisters
        from .store import match_id

        with self.lock:
            row = self.store.finding(finding_id)
            if row is None:
                return {"ok": False, "error": "That match was not found."}
            sisters = load_sisters(self.data_dir, self.store)
            g = sisters.group_of(row["domain"]) if row["page_url"] else None
            licensed = load_approvals(self.data_dir, sisters).licensed(row["photographer"], row["domain"]) \
                if row["page_url"] else None
            others = [((o["page_url"] or o["image_url"]), match_id(o["id"], o["found_at"]), o["found_at"])
                      for o in self.store.other_findings(row["image_id"], row["page_url"] or row["image_url"])]
            path = build_pdf(row, self.store, self.data_dir,
                             {"company": g.name if g else None, "sister_sites": g.domains if g else [],
                              "licensed": licensed, "other_pages": others})
        return {"ok": True, "path": str(path), "name": path.name}

    # ---------- things on this PC ----------
    def open_path(self, path: str, select=False) -> dict:
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "That file is not there any more."}
        try:
            if sys.platform.startswith("win"):
                if select:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                else:
                    os.startfile(path)  # noqa: S606 (opens with the normal program)
            else:
                webbrowser.open(Path(path).resolve().as_uri())
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def pick(self, kind: str) -> dict:
        """Opens the normal Windows "choose a folder / file" box."""
        result = {}

        def ask():
            try:
                _fix_tcl_paths()
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                if kind == "folder":
                    result["path"] = filedialog.askdirectory(parent=root, title="Choose a folder of your images")
                elif kind == "list":
                    result["path"] = filedialog.askopenfilename(
                        parent=root, title="Choose a list of links", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
                else:
                    exts = " ".join("*" + e for e in sorted(config.IMAGE_EXTENSIONS))
                    result["path"] = filedialog.askopenfilename(
                        parent=root, title="Choose a picture", filetypes=[("Pictures", exts), ("All files", "*.*")])
                root.destroy()
            except Exception as e:
                result["error"] = f"Could not open the chooser ({e}). Paste the path instead."

        t = threading.Thread(target=ask)
        t.start()
        t.join()
        if result.get("error") and sys.platform.startswith("win"):
            ps = _powershell_pick(kind)       # plan B: the Windows chooser through PowerShell
            if ps is not None:
                result = {"path": ps}
        if result.get("error"):
            return {"ok": False, "error": result["error"]}
        path = os.path.normpath(result["path"]) if result.get("path") else ""
        out = {"ok": True, "path": path}
        if kind == "list" and path:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    out["links"] = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            except OSError as e:
                return {"ok": False, "error": str(e)}
        return out

    # ---------- pictures ----------
    def our_picture(self, image_id: int, size: int) -> bytes | None:
        from .fingerprint import open_image

        size = 360 if size <= 360 else 900
        cache = self.data_dir / "cache" / f"ours_{image_id}_{size}.jpg"
        if cache.exists():
            return cache.read_bytes()
        with self.lock:
            row = self.store.db.execute("SELECT path FROM images WHERE id=?", (int(image_id),)).fetchone()
        if not row:
            return None
        try:
            from PIL import Image, ImageOps

            img = Image.open(row[0])
            try:
                img.draft("RGB", (size, size))
            except Exception:
                pass
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img, _ = open_image(row[0])
            img.thumbnail((size, size))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85)
            data = out.getvalue()
            cache.write_bytes(data)
            return data
        except Exception:
            return None

    def their_picture(self, finding_id: int, size: int) -> tuple[bytes, str] | None:
        from .proof import their_picture

        with self.lock:
            row = self.store.db.execute("SELECT id, evidence_dir, thumb FROM findings WHERE id=?",
                                        (int(finding_id),)).fetchone()
        if not row:
            return None
        src = their_picture(row)
        if isinstance(src, str) and size > 400:
            try:
                from PIL import Image, ImageOps

                img = ImageOps.exif_transpose(Image.open(src))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.thumbnail((900, 900))
                out = io.BytesIO()
                img.save(out, "JPEG", quality=85)
                return out.getvalue(), "image/jpeg"
            except Exception:
                pass
        if row["thumb"]:
            return bytes(row["thumb"]), "image/jpeg"
        return None


# ------------------------------------------------------------------ web server

def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PixelProof"

        def log_message(self, *a):  # keep the black window quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json", cache=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode("utf-8"))

        def _authed(self, qs) -> bool:
            tok = self.headers.get("X-PixelProof") or (qs.get("t") or [""])[0]
            return secrets.compare_digest(tok, app.token)

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            path = u.path
            if path in ("/", "/index.html"):
                if not self._authed(qs):
                    return self._send(403, b"Open PixelProof with PixelProof.bat", "text/plain")
                html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/ui/"):
                f = (UI_DIR / path[4:]).resolve()
                if UI_DIR.resolve() not in f.parents or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if f.suffix == ".woff2":
                    ctype = "font/woff2"
                return self._send(200, f.read_bytes(), ctype, cache=f.suffix == ".woff2")
            if not self._authed(qs):
                return self._send(403, b"forbidden", "text/plain")
            app.last_seen = time.time()
            m = re.fullmatch(r"/img/(ours|theirs)/(\d+)", path)
            if m:
                size = int((qs.get("s") or ["360"])[0])
                if m.group(1) == "ours":
                    data, ctype = app.our_picture(int(m.group(2)), size), "image/jpeg"
                else:
                    got = app.their_picture(int(m.group(2)), size)
                    data, ctype = (got if got else (None, None))
                if not data:
                    return self._send(404, b"", "text/plain")
                return self._send(200, data, ctype, cache=True)
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/job":
                return self._json(app.job.as_dict() if app.job else None)
            if path == "/api/joblog":
                return self._send(200, (app.job.text() if app.job else "").encode("utf-8"), "text/plain; charset=utf-8")
            if path == "/api/ping":
                return self._json({"ok": True})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            if not self._authed(parse_qs(u.query)):
                return self._send(403, b"forbidden", "text/plain")
            if u.path != "/api/bye":
                app.last_seen = time.time()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                return self._json({"ok": False, "error": "bad request"}, 400)
            try:
                return self._json(route(app, u.path, body))
            except Exception as e:
                traceback.print_exc()
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def route(app: App, path: str, b: dict) -> dict:
    sites = b.get("sites") or ([b["site"]] if b.get("site") else [])
    if path == "/api/check":
        return app.check(b.get("targets") or [b.get("target", "")])
    if path == "/api/crawl":
        return app.crawl(b.get("sites") or None, int(b.get("pages") or 20), int(b.get("workers") or 12))
    if path == "/api/unlikely":
        return app.unlikely(bool(b.get("apply")))
    if path == "/api/news":
        return app.news(b.get("hours") or 3, bool(b.get("us_only", True)))
    if path == "/api/index":
        return app.index(b.get("folder", ""))
    if path == "/api/bye":
        app.bye_at = time.time()
        return {"ok": True}
    if path == "/api/stop":
        if app.job:
            app.job.stop = True
        return {"ok": True}
    if path == "/api/status":
        return app.set_status(b.get("ids") or [], b.get("status", "new"))
    if path == "/api/ban":
        return app.ban(sites, unban=bool(b.get("remove")))
    if path == "/api/approve":
        return app.approve(b.get("photographer", ""), sites, remove=bool(b.get("remove")))
    if path == "/api/sister":
        return app.sister(b.get("name", ""), sites, remove=bool(b.get("remove")))
    if path == "/api/watch":
        return app.watch(sites, remove=bool(b.get("remove")))
    if path == "/api/import":
        return app.import_sites(b.get("text", ""), b.get("into", "watch"))
    if path == "/api/proof":
        res = app.proof(int(b.get("id", 0)))
        if res.get("ok") and b.get("open", True):
            app.open_path(res["path"])
        return res
    if path == "/api/open-url":
        url = str(b.get("url", ""))
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "Not a web link."}
        webbrowser.open(url)
        return {"ok": True}
    if path == "/api/open-path":
        what = b.get("what")
        target = {"proofs": app.data_dir / "proofs", "evidence": app.data_dir / "evidence",
                  "data": app.data_dir}.get(what) if what else None
        if b.get("finding"):
            row = app.store.finding(int(b["finding"]))
            if row is None:
                return {"ok": False, "error": "That match was not found."}
            target = {"ours": row["our_path"], "evidence": row["evidence_dir"], "proof": row["proof_path"]}.get(b.get("which"))
        if not target:
            return {"ok": False, "error": "Nothing to open."}
        return app.open_path(str(target), select=bool(b.get("select")))
    if path == "/api/pick":
        return app.pick(b.get("kind", "folder"))
    return {"ok": False, "error": "unknown action"}


# ------------------------------------------------------------------ app window

def _find_browser() -> list[str] | None:
    """Edge is on every Windows 10/11 PC. Chrome works too."""
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for c in candidates:
        if c.is_file():
            return [str(c)]
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return [p]
    return None


def open_window(url: str) -> None:
    exe = _find_browser()
    if exe:
        try:
            subprocess.Popen(exe + [f"--app={url}", "--window-size=1440,920", "--new-window"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def serve(data_dir: Path, open_app=True, port=0, clip_factory=None, configure=None) -> None:
    app = App(data_dir, clip_factory=clip_factory)
    if configure:
        configure(app)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{server.server_port}/?t={app.token}"
    print(f"PixelProof {config.VERSION} is running.")
    print("The app window opens by itself. Keep this black window open while you use it.")
    print("When you close the app window, this window closes too.\n")
    print(f"If the window did not open, paste this into Edge or Chrome:\n  {url}\n")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_app:
        open_window(url)
    try:
        while True:
            time.sleep(2)
            busy = app.job is not None and app.job.state == "running"
            t = time.time()
            if not open_app or busy:
                continue
            if app.bye_at and app.last_seen < app.bye_at and t - app.bye_at > BYE_GRACE_SECONDS:
                break
            if t - app.last_seen > IDLE_EXIT_SECONDS:
                break
    except KeyboardInterrupt:
        pass
    print("PixelProof closed.")
    server.shutdown()
