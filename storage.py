
"""SQLite catalogue of discovered URLs and unique books."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .fingerprints import Fingerprint, jaccard
from .schemas import Book


SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT DEFAULT '',
    year INTEGER,
    edition TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    department TEXT DEFAULT '',
    courses TEXT DEFAULT '',
    level TEXT DEFAULT '',
    description TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    file_sha256 TEXT UNIQUE,
    text_sha256 TEXT UNIQUE,
    fmt TEXT DEFAULT 'pdf',
    size_bytes INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    shingles BLOB,
    storage_path TEXT DEFAULT '',
    license TEXT DEFAULT 'unknown',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_books_word_count ON books(word_count);
CREATE INDEX IF NOT EXISTS idx_books_subject ON books(subject);

CREATE TABLE IF NOT EXISTS discovery (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT DEFAULT '',
    authors TEXT DEFAULT '',
    source TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    category TEXT DEFAULT '',
    year INTEGER,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_status ON discovery(status);
"""


def _db_path(database_url: str) -> str:
    path = database_url

    for prefix in ("sqlite:///", "sqlite://"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    return path or ":memory:"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(
        self,
        database_url: str = "sqlite:///./docreaper.db",
    ):
        self.conn = sqlite3.connect(_db_path(database_url))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- discovery queue -------------------------------------------------

    def add_discovery(self, rec) -> bool:
        """Queue a discovered URL. Returns False if already queued."""

        cur = self.conn.execute(
            "INSERT OR IGNORE INTO discovery "
            "(url, title, authors, source, subject, category, year, "
            "description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.url,
                rec.title,
                rec.authors,
                rec.source,
                rec.subject,
                rec.category,
                rec.year,
                rec.description,
                _now(),
            ),
        )

        self.conn.commit()
        return cur.rowcount > 0

    def pending_discoveries(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM discovery "
            "WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        )

        return [dict(row) for row in rows]

    def set_discovery_status(
        self,
        url: str,
        status: str,
        error: str = "",
    ) -> None:
        self.conn.execute(
            "UPDATE discovery SET status = ?, error = ? WHERE url = ?",
            (status, error, url),
        )

        self.conn.commit()

    # -- books -----------------------------------------------------------

    def count_books(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]

    def has_file_sha256(self, file_sha256: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM books WHERE file_sha256 = ?",
            (file_sha256,),
        ).fetchone()

        return row is not None

    def has_text_sha256(self, text_sha256: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM books WHERE text_sha256 = ?",
            (text_sha256,),
        ).fetchone()

        return row is not None

    def add_book(
        self,
        book: Book,
        fp: Fingerprint,
        storage_path: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO books "
            "(title, authors, year, edition, subject, department, "
            "courses, level, description, source_url, file_sha256, "
            "text_sha256, fmt, size_bytes, word_count, shingles, "
            "storage_path, license, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book.title,
                book.authors,
                book.year,
                book.edition,
                book.subject,
                book.department,
                book.courses,
                book.level,
                book.description,
                book.source_url,
                fp.file_sha256,
                fp.text_sha256,
                book.fmt,
                book.size_bytes,
                fp.word_count,
                fp.pack_shingles(),
                storage_path,
                book.license,
                json.dumps(book.metadata),
                _now(),
            ),
        )

        self.conn.commit()
        return cur.lastrowid

    def near_duplicate(
        self,
        shingles: frozenset[int],
        word_count: int,
        threshold: float,
    ) -> dict | None:
        """Linear Jaccard scan with a word-count prefilter."""

        lo = word_count // 2
        hi = max(word_count * 2, 1)

        rows = self.conn.execute(
            "SELECT id, title, shingles FROM books "
            "WHERE word_count BETWEEN ? AND ?",
            (lo, hi),
        )

        for row in rows:
            score = jaccard(
                shingles,
                Fingerprint.unpack_shingles(row["shingles"]),
            )

            if score is not None and score >= threshold:
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "score": score,
                }

        return None

    def recent_books(self, limit: int = 10) -> list[dict]:
        """Return recently catalogued books for the dashboard."""

        rows = self.conn.execute(
            """
            SELECT
                id,
                title,
                authors,
                year,
                subject,
                fmt,
                size_bytes,
                word_count,
                license,
                storage_path,
                created_at
            FROM books
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [dict(row) for row in rows]

    def stats(self) -> dict:
        def count(table: str, where: str = "") -> int:
            sql = f"SELECT COUNT(*) FROM {table} {where}"
            return self.conn.execute(sql).fetchone()[0]

        by_subject = self.conn.execute(
            "SELECT subject, COUNT(*) AS n FROM books "
            "GROUP BY subject ORDER BY n DESC"
        )

        return {
            "books": count("books"),
            "by_subject": {
                row["subject"]: row["n"]
                for row in by_subject
            },
            "discovery_pending": count(
                "discovery", "WHERE status = 'pending'"
            ),
            "discovery_kept": count(
                "discovery", "WHERE status = 'kept'"
            ),
            "discovery_duplicate": count(
                "discovery", "WHERE status = 'duplicate'"
            ),
            "discovery_failed": count(
                "discovery", "WHERE status = 'failed'"
            ),
        }
