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
"""


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
    def save_finding(self, page_url, image_url, domain, image_id, category, clip_sim, hash_dist, note) -> tuple[int, str]:
        found_at = now()
        cur = self.db.execute(
            """INSERT INTO findings(found_at, page_url, image_url, domain, image_id, category, clip_sim, hash_dist, note)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (found_at, page_url, image_url, domain, image_id, category, clip_sim, hash_dist, note),
        )
        return cur.lastrowid, found_at

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
