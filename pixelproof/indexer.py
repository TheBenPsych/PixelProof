"""Step 1: fingerprint every image in a folder (works with Google Drive for desktop).

It reads one file at a time and never copies your images. It remembers what is done,
so you can stop it (Ctrl+C or close the window) and run it again to carry on.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import library
from .fingerprint import open_image, phash
from .store import Store


@dataclass
class IndexResult:
    found: int = 0
    skipped: int = 0
    added: int = 0
    failed: int = 0
    seconds: float = 0.0
    stopped: bool = False


def _load(path: str, root: str, preprocess):
    try:
        st = os.stat(path)
        img, (w, h) = open_image(path)
        return {
            "path": path, "size": st.st_size, "mtime": st.st_mtime,
            "info": library.describe(path, root), "w": w, "h": h,
            "phash": phash(img), "tensor": preprocess(img),
        }
    except Exception as e:  # unreadable, not really an image, Drive offline, etc.
        return {"path": path, "error": f"{type(e).__name__}: {e}"}


def index_folder(root: str, store: Store, clip, batch_size: int = 64, workers: int = 8,
                 log=print, progress=True, on_progress=None, should_stop=None) -> IndexResult:
    """on_progress(done, total) and should_stop() are used by the desktop app."""
    root = os.path.abspath(root)
    res = IndexResult()
    start = time.time()
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Folder not found: {root}")

    log(f"Looking for images in {root} ...")
    known = store.known_files()
    todo, refresh = [], []
    for path in library.scan(root):
        res.found += 1
        try:
            st = os.stat(path)
        except OSError:
            continue
        if known.get(path) == (st.st_size, st.st_mtime):
            res.skipped += 1
            refresh.append(path)
        else:
            todo.append(path)
        if res.found % 5000 == 0:
            if progress:
                log(f"  ...found {res.found:,} so far")
            if on_progress:
                on_progress(0, 0, res.found)
            if should_stop and should_stop():
                res.stopped = True
                res.seconds = time.time() - start
                return res
    # Re-read names for files already done (cheap, no image opened) so naming fixes apply.
    for path in refresh:
        store.update_info(path, library.describe(path, root))
    store.commit()
    log(f"Found {res.found:,} images. Already done: {res.skipped:,}. To fingerprint now: {len(todo):,}.")
    if not todo:
        res.seconds = time.time() - start
        return res

    log(f"Using your {clip.device_label()}.")
    bar = None
    if progress:
        from tqdm import tqdm

        bar = tqdm(total=len(todo), unit="img", smoothing=0.05)
    store.mark_dirty()
    store.commit()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i in range(0, len(todo), batch_size):
                items = list(pool.map(lambda p: _load(p, root, clip.preprocess), todo[i:i + batch_size]))
                good = [it for it in items if "error" not in it]
                for it in items:
                    if "error" in it:
                        store.save_error(it["path"], it["error"])
                        res.failed += 1
                if good:
                    vecs = clip.embed_tensors([it["tensor"] for it in good])
                    for it, vec in zip(good, vecs):
                        store.save_image(it["path"], it["size"], it["mtime"], it["info"],
                                         it["w"], it["h"], it["phash"], vec)
                    res.added += len(good)
                store.commit()
                if bar:
                    bar.update(len(items))
                if on_progress:
                    on_progress(min(i + batch_size, len(todo)), len(todo), res.found)
                if should_stop and should_stop():
                    res.stopped = True
                    log("Stopped. Your progress is saved. Start the same folder again to carry on.")
                    break
    except KeyboardInterrupt:
        res.stopped = True
        store.commit()
        log("\nStopped. Your progress is saved. Run the same command again to carry on.")
    finally:
        if bar:
            bar.close()

    log("Updating the fast search index ...")
    store.build_indexes(clip.dim)
    res.seconds = time.time() - start
    return res
