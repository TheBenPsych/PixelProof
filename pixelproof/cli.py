"""PixelProof commands.

  pixelproof doctor                     check that everything is installed
  pixelproof index "G:\\My Drive\\Photographers"   fingerprint your images
  pixelproof check https://example.com/page       check one page (or a .txt list of links, or a picture file)
  pixelproof status                     what is in your library
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from . import config


def _clip(data_dir):
    from .fingerprint import Clip

    import logging
    import warnings

    warnings.filterwarnings("ignore", message=".*QuickGELU.*")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        from huggingface_hub.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
    except Exception:
        pass
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    print("Loading the AI fingerprint model (the first time this downloads about 350 MB) ...")
    return Clip(cache_dir=data_dir / "models")


def _library_model(store) -> str | None:
    """Which AI model the library was fingerprinted with (None if the library is empty)."""
    if store.count() == 0:
        return None
    return store.get_meta("clip_model", "ViT-B-32/openai")


def cmd_doctor(args, data_dir) -> int:
    ok = True
    print(f"PixelProof {config.VERSION}")
    print(f"Python {sys.version.split()[0]}")
    print(f"Data folder: {data_dir}")
    try:
        import torch

        print(f"PyTorch {torch.__version__}")
        if torch.cuda.is_available():
            print(f"Graphics card: {torch.cuda.get_device_name(0)}  (good, fingerprinting will be fast)")
        else:
            print("Graphics card: NOT found. It will still work, just slower.")
            print("  If you have an NVIDIA card, run install.bat again.")
    except Exception as e:
        ok = False
        print(f"PyTorch: problem ({e})")
    for mod in ("faiss", "open_clip", "imagehash", "PIL", "requests", "bs4"):
        try:
            __import__(mod)
            print(f"{mod}: ok")
        except Exception as e:
            ok = False
            print(f"{mod}: MISSING ({e})")
    if ok and not args.skip_model:
        try:
            clip = _clip(data_dir)
            print(f"AI model: ok, running on your {clip.device_label()}")
        except Exception as e:
            ok = False
            print(f"AI model: could not load ({e})")
    print("\nAll good!" if ok else "\nSomething is missing. Run install.bat again.")
    return 0 if ok else 1


def cmd_index(args, data_dir) -> int:
    from .indexer import index_folder
    from .store import Store

    store = Store(data_dir)
    clip = _clip(data_dir)
    old = _library_model(store)
    if args.rebuild or (old and old != clip.name):
        if not args.rebuild:
            print("PixelProof has a better AI model setting now, so every image gets a fresh fingerprint (one time).")
        store.db.execute("UPDATE images SET clip=NULL")
        store.mark_dirty()
        store.commit()
    store.set_meta("clip_model", clip.name)
    store.commit()
    total_found = 0
    for folder in args.folders:
        folder = folder.strip().strip('"')
        res = index_folder(folder, store, clip, batch_size=args.batch)
        total_found += res.found
        rate = res.added / res.seconds if res.seconds else 0
        print(f"\nDone with {folder}")
        print(f"  New or changed images fingerprinted: {res.added:,}  ({rate:.0f} per second)")
        print(f"  Already done before: {res.skipped:,}")
        if res.failed:
            print(f"  Could not read: {res.failed:,}  (see 'pixelproof status --errors')")
        if res.stopped:
            return 1
    print(f"\nYour library now holds {store.count():,} images.")
    return 0


def _targets(raw: list[str]) -> list[str]:
    out = []
    for t in raw:
        t = t.strip().strip('"')
        if t.lower().endswith(".txt") and os.path.isfile(t):
            with open(t, encoding="utf-8") as f:
                out.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))
        else:
            out.append(t)
    return out


def cmd_check(args, data_dir) -> int:
    from .check import check_target
    from .report import write_report
    from .store import Store

    store = Store(data_dir)
    if store.count() == 0:
        print("Your library is empty. Index a folder of your images first (option 1 in the menu).")
        return 1
    clip = _clip(data_dir)
    if _library_model(store) != clip.name:
        print("PixelProof was updated and your library needs fresh fingerprints first.")
        print("Run option 1 (Fingerprint a folder) on your images folder again, then check this page.")
        return 1
    store.load_indexes(clip.dim)
    any_match = False
    for target in _targets(args.targets):
        print(f"\nChecking {target}")
        result = check_target(target, store, clip, data_dir)
        path = write_report(result, data_dir)
        n = len(result.groups)
        any_match |= n > 0
        if not result.error and not result.banned:
            print(f"  {len(result.queries)} pictures checked, {n} of your images found.")
        print(f"  Report: {path}")
        if not args.no_open:
            webbrowser.open(path.as_uri())
    return 0


def cmd_status(args, data_dir) -> int:
    from .store import Store

    store = Store(data_dir)
    print(f"Images in your library: {store.count():,}\n")
    rows = store.db.execute(
        "SELECT COALESCE(photographer,'(unknown)') p, COUNT(*) n, MIN(year) y1, MAX(year) y2 "
        "FROM images WHERE clip IS NOT NULL GROUP BY p ORDER BY n DESC").fetchall()
    for r in rows:
        years = f"{r['y1']}-{r['y2']}" if r["y1"] else ""
        print(f"  {r['p']:<40} {r['n']:>10,}   {years}")
    n_err = store.db.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    if n_err:
        print(f"\n{n_err:,} files could not be read.")
        if args.errors:
            for r in store.db.execute("SELECT path, error FROM errors ORDER BY path LIMIT 200"):
                print(f"  {r[0]}\n     {r[1]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pixelproof", description="Find where your images are used online.")
    p.add_argument("--data", help="data folder (default: the data folder next to the app)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check that everything is installed")
    d.add_argument("--skip-model", action="store_true")

    i = sub.add_parser("index", help="fingerprint every image in a folder")
    i.add_argument("folders", nargs="+")
    i.add_argument("--batch", type=int, default=64)
    i.add_argument("--rebuild", action="store_true", help="redo every image (after changing the AI model)")

    c = sub.add_parser("check", help="check one page, a .txt list of links, or a picture file")
    c.add_argument("targets", nargs="+")
    c.add_argument("--no-open", action="store_true", help="don't open the report in the browser")

    s = sub.add_parser("status", help="show what is in your library")
    s.add_argument("--errors", action="store_true")

    args = p.parse_args(argv)
    data_dir = config.data_dir(args.data)
    handler = {"doctor": cmd_doctor, "index": cmd_index, "check": cmd_check, "status": cmd_status}[args.cmd]
    try:
        return handler(args, data_dir)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
    except FileNotFoundError as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
