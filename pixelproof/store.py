"""The library index: a small database of fingerprints plus two fast FAISS search indexes.

data\\library.db      one row per image of yours (path, photographer, year, quarter, fingerprints)
data\\clip.faiss      fast look-alike search over CLIP fingerprints
data\\hash.faiss      fast search over perceptual hashes
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size INTEGER, mtime REAL,
    photographer TEXT, year INTEGER, quarter TEXT, file_date TEXT,
    width INTEGER, height INTEGER,
    phash BLOB, clip BLOB,
    indexed_at TEXT
);
CREATE INDEX IF NOT EXISTS images_photographer ON images(photographer, year, quarter);
CREATE TABLE IF NOT EXISTS errors (path TEXT PRIMARY KEY, error TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    found_at TEXT, page_url TEXT, image_url TEXT, domain TEXT,
    image_id INTEGER, category TEXT, clip_sim REAL, hash_dist INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL, domain TEXT, path_key TEXT,
    first_checked TEXT, last_checked TEXT, pictures INTEGER, found INTEGER
);
CREATE INDEX IF NOT EXISTS pages_path_key ON pages(path_key);
"""

# Columns added after step 1. Old libraries get them the first time step 2 opens them.
FINDING_COLUMNS = {"last_seen": "TEXT", "path_key": "TEXT", "thumb": "BLOB",
                   "width": "INTEGER", "height": "INTEGER", "times_seen": "INTEGER DEFAULT 1"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def match_id(finding_id: int, found_at: str) -> str:
    return f"PX-{found_at[:4]}-{finding_id:06d}"


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.db = sqlite3.connect(self.dir / "library.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(findings)")}
        for col, kind in FINDING_COLUMNS.items():
            if col not in have:
                self.db.execute(f"ALTER TABLE findings ADD COLUMN {col} {kind}")
        self.db.execute("CREATE INDEX IF NOT EXISTS findings_image ON findings(image_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS findings_page ON findings(page_url, image_id)")
        self.db.commit()
        self._clip_index = None
        self._hash_index = None

    # ---------- meta ----------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    # ---------- images ----------
    def known_files(self) -> dict[str, tuple[int, float]]:
        rows = self.db.execute("SELECT path, size, mtime FROM images WHERE clip IS NOT NULL")
        return {r[0]: (r[1], r[2]) for r in rows}

    def save_image(self, path, size, mtime, info, width, height, phash: bytes, clip: np.ndarray) -> None:
        self.db.execute(
            """INSERT INTO images(path, size, mtime, photographer, year, quarter, file_date,
                                  width, height, phash, clip, indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                 photographer=excluded.photographer, year=excluded.year, quarter=excluded.quarter,
                 file_date=excluded.file_date, width=excluded.width, height=excluded.height,
                 phash=excluded.phash, clip=excluded.clip, indexed_at=excluded.indexed_at""",
            (path, size, mtime, info.photographer, info.year, info.quarter, info.file_date,
             width, height, phash, clip.astype(np.float32).tobytes(), now()),
        )
        self.db.execute("DELETE FROM errors WHERE path=?", (path,))

    def update_info(self, path, info) -> None:
        self.db.execute("UPDATE images SET photographer=?, year=?, quarter=?, file_date=? WHERE path=?",
                        (info.photographer, info.year, info.quarter, info.file_date, path))

    def save_error(self, path: str, error: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO errors(path, error, at) VALUES(?,?,?)", (path, error[:500], now()))

    def commit(self) -> None:
        self.db.commit()

    def get_images(self, ids) -> dict[int, sqlite3.Row]:
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        q = "SELECT * FROM images WHERE id IN (%s)" % ",".join("?" * len(ids))
        return {r["id"]: r for r in self.db.execute(q, ids)}

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM images WHERE clip IS NOT NULL").fetchone()[0]

    # ---------- findings ----------
    def save_finding(self, page_url, image_url, domain, image_id, category, clip_sim, hash_dist, note,
                     path_key=None, thumb=None, width=None, height=None) -> tuple[int, str]:
        """Saves one of our images found on one page. Checking the same page again keeps the
        same match ID and first-found date, and only updates "last seen"."""
        seen = now()
        where = page_url or image_url
        row = self.db.execute(
            "SELECT id, found_at FROM findings WHERE COALESCE(NULLIF(page_url,''), image_url)=? AND image_id=? "
            "ORDER BY id LIMIT 1", (where, image_id)).fetchone()
        if row:
            self.db.execute(
                """UPDATE findings SET last_seen=?, times_seen=COALESCE(times_seen,1)+1, image_url=?, domain=?,
                   category=?, clip_sim=?, hash_dist=?, note=?, path_key=?, thumb=COALESCE(?, thumb),
                   width=?, height=? WHERE id=?""",
                (seen, image_url, domain, category, clip_sim, hash_dist, note, path_key, thumb, width, height, row[0]))
            return row[0], row[1]
        cur = self.db.execute(
            """INSERT INTO findings(found_at, last_seen, page_url, image_url, domain, image_id, category, clip_sim,
                                    hash_dist, note, path_key, thumb, width, height, times_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (seen, seen, page_url, image_url, domain, image_id, category, clip_sim, hash_dist, note,
             path_key, thumb, width, height),
        )
        return cur.lastrowid, seen

    def other_findings(self, image_id: int, not_page: str) -> list[sqlite3.Row]:
        """Every other page where this image of ours was found before (repeat copies)."""
        return self.db.execute(
            "SELECT * FROM findings WHERE image_id=? AND COALESCE(NULLIF(page_url,''), image_url)<>? "
            "ORDER BY found_at", (image_id, not_page)).fetchall()

    def all_findings(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT f.*, i.path AS our_path, i.photographer, i.year, i.quarter, i.file_date, "
            "i.width AS our_w, i.height AS our_h FROM findings f LEFT JOIN images i ON i.id=f.image_id "
            "ORDER BY f.found_at").fetchall()

    # ---------- pages we checked (used to spot sister sites) ----------
    def save_page(self, url, domain, path_key, pictures, found) -> None:
        t = now()
        self.db.execute(
            """INSERT INTO pages(url, domain, path_key, first_checked, last_checked, pictures, found)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET domain=excluded.domain, path_key=excluded.path_key,
                 last_checked=excluded.last_checked, pictures=excluded.pictures, found=excluded.found""",
            (url, domain, path_key, t, t, pictures, found))

    def page_keys(self) -> list[tuple[str, str, str]]:
        rows = self.db.execute(
            "SELECT url, domain, path_key FROM pages WHERE path_key IS NOT NULL "
            "UNION SELECT page_url, domain, path_key FROM findings WHERE path_key IS NOT NULL AND page_url<>''")
        return [(r[0], r[1], r[2]) for r in rows]

    def pages_with_key(self, path_key: str, not_url: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM pages WHERE path_key=? AND url<>? ORDER BY first_checked", (path_key, not_url)).fetchall()

    def count_pages(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    # ---------- FAISS search indexes ----------
    def mark_dirty(self) -> None:
        self.set_meta("index_dirty", "1")

    def build_indexes(self, dim: int, chunk: int = 50_000) -> int:
        """(Re)build the fast search indexes from the database."""
        import faiss

        clip_index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        hash_index = faiss.IndexBinaryIDMap(faiss.IndexBinaryFlat(64))
        last_id, total = 0, 0
        while True:
            rows = self.db.execute(
                "SELECT id, phash, clip FROM images WHERE clip IS NOT NULL AND id > ? ORDER BY id LIMIT ?",
                (last_id, chunk),
            ).fetchall()
            if not rows:
                break
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            vecs = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            if vecs.shape[1] != dim:
                raise RuntimeError("The index was made with a different AI model. Run: pixelproof index --rebuild")
            hashes = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.uint8).reshape(len(rows), 8)
            clip_index.add_with_ids(np.ascontiguousarray(vecs), ids)
            hash_index.add_with_ids(np.ascontiguousarray(hashes), ids)
            last_id, total = int(ids[-1]), total + len(rows)
        self._write(self.dir / "clip.faiss", faiss.serialize_index(clip_index))
        self._write(self.dir / "hash.faiss", faiss.serialize_index_binary(hash_index))
        self.set_meta("index_dirty", "0")
        self.set_meta("index_dim", str(dim))
        self.commit()
        self._clip_index, self._hash_index = clip_index, hash_index
        return total

    @staticmethod
    def _write(path: Path, arr: np.ndarray) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(arr.tobytes())
        tmp.replace(path)

    def load_indexes(self, dim: int) -> None:
        import faiss

        clip_path, hash_path = self.dir / "clip.faiss", self.dir / "hash.faiss"
        if (self.get_meta("index_dirty", "1") == "1" or not clip_path.exists() or not hash_path.exists()
                or self.get_meta("index_dim") != str(dim)):
            self.build_indexes(dim)
            return
        self._clip_index = faiss.deserialize_index(np.fromfile(clip_path, dtype=np.uint8))
        self._hash_index = faiss.deserialize_index_binary(np.fromfile(hash_path, dtype=np.uint8))

    def search_clip(self, vecs: np.ndarray, k: int):
        if self._clip_index.ntotal == 0:
            return np.zeros((len(vecs), 0)), np.zeros((len(vecs), 0), dtype=np.int64)
        return self._clip_index.search(np.ascontiguousarray(vecs, dtype=np.float32), min(k, self._clip_index.ntotal))

    def search_hash(self, hashes: list[bytes], k: int):
        if self._hash_index.ntotal == 0:
            return np.zeros((len(hashes), 0)), np.zeros((len(hashes), 0), dtype=np.int64)
        arr = np.frombuffer(b"".join(hashes), dtype=np.uint8).reshape(len(hashes), 8)
        return self._hash_index.search(np.ascontiguousarray(arr), min(k, self._hash_index.ntotal))
