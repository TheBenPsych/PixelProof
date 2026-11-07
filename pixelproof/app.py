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
        self.done = self.total = self.found = 0
        self.result: dict = {}
        self.stop = False
        self.started = time.time()

    def log(self, *parts):
        text = " ".join(str(p) for p in parts).strip()
        if text:
            self.lines.append(text)
            del self.lines[:-200]

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

    def crawl(self, sites: list[str] | None, pages_per_site: int = 20, workers: int = 4) -> dict:
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
        targets = [s for s in (sites or watch) if s]
        if not targets:
            return {"ok": False, "error": "Your watch list is empty. Add sites in Site lists first."}
        if not sites:  # sites never crawled first, then the ones crawled longest ago
            targets.sort(key=lambda d: done.get(d, ""))
        pages_per_site = max(1, min(int(pages_per_site or 20), 500))
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
                    with lock:
                        return clip.embed(images)

                def embed_tensors(self, tensors):
                    with lock:
                        return clip.embed_tensors(tensors)

                def device_label(self):
                    return clip.device_label()

            shared = SharedClip()
            seen: set = set()
            job.total = len(targets)
            job.result = {"new_ids": [], "sites": 0, "pages": 0, "matches": 0, "skipped": 0}
            big = [d for d in targets if d in TOO_BIG]
            if big:
                job.log(f"Skipping {len(big)} giant platforms that can't be crawled (like {', '.join(big[:3])}).")

            def one(domain):
                if job.stop:
                    return
                st = Store(self.data_dir)
                st._clip_index, st._hash_index = main._clip_index, main._hash_index
                try:
                    r = crawl_site(domain, st, shared, self.data_dir, pages_per_site=pages_per_site,
                                   log=job.log, should_stop=lambda: job.stop, seen_images=seen,
                                   base_url=self.base_urls.get(domain), delay=self.crawl_delay)
                except Exception as e:  # one broken site never stops the run
                    from .crawl import SiteResult

                    r = SiteResult(domain, "error", f"{type(e).__name__}: {e}"[:200])
                save_site(st, r)
                st.db.close()
                with lock:
                    job.done += 1
                    res = job.result
                    res["sites"] += 1
                    res["pages"] += r.checked
                    res["matches"] += r.matches
                    res["skipped"] += r.status == "skipped"
                    res["new_ids"] += r.new_ids
                    job.found = len(res["new_ids"])
                if r.status == "error":
                    job.log(f"{domain}: {r.note}")
                elif r.status == "done":
                    job.log(f"{domain}: done. {r.checked} page{'s' if r.checked != 1 else ''} checked, "
                            f"{r.matches} of your images found.")

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(one, targets))
            res = job.result
            job.log(f"Finished: {res['sites']} sites, {res['pages']} pages checked, "
                    f"{len(res['new_ids'])} new matches.")
            main.db.close()

        label = targets[0] if len(targets) == 1 else f"{len(targets)} sites"
        return self.start_job("crawl", label, work)

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
        return app.crawl(b.get("sites") or None, int(b.get("pages") or 20))
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
