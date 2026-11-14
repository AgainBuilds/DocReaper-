
from __future__ import annotations

import pytest

from docreaper.fingerprints import fingerprint
from docreaper.schemas import Book, DiscoveryRecord
from docreaper.storage import Store


@pytest.fixture
def store(tmp_path):
    """Create an isolated SQLite database for each test."""
    database_path = tmp_path / "test_docreaper.db"
    database_url = f"sqlite:///{database_path}"

    db = Store(database_url)

    yield db

    db.close()


def test_database_initializes(store):
    """All required tables should exist after initialization."""
    tables = {
        row["name"]
        for row in store.conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }

    assert {
        "books",
        "discovery",
        "worker_state",
        "run_history",
        "engine_logs",
    }.issubset(tables)


def test_worker_state_is_initialized(store):
    """A default worker state should be created automatically."""
    state = store.get_worker_state()

    assert state["id"] == 1
    assert state["status"] == "idle"
    assert state["pause_requested"] == 0
    assert state["stop_requested"] == 0


def test_discovery_insertion_and_duplicate_prevention(store):
    """The same discovery URL must not be inserted twice."""
    record = DiscoveryRecord(
        url="https://example.org/book.pdf",
        title="Introduction to Mathematics",
        authors="Test Author",
        source="test",
        subject="Mathematics",
        category="Academic",
        year=2024,
        description="A test academic book.",
    )

    assert store.add_discovery(record) is True
    assert store.add_discovery(record) is False

    pending = store.pending_discoveries()

    assert len(pending) == 1
    assert pending[0]["url"] == record.url
    assert pending[0]["title"] == record.title


def test_discovery_status_and_attempt_tracking(store):
    """Discovery attempts and status changes should persist."""
    record = DiscoveryRecord(
        url="https://example.org/physics.pdf",
        title="Physics Fundamentals",
    )

    store.add_discovery(record)
    store.mark_discovery_attempt(record.url)
    store.set_discovery_status(
        record.url,
        "kept",
    )

    row = store.conn.execute(
        "SELECT * FROM discovery WHERE url = ?",
        (record.url,),
    ).fetchone()

    assert row["attempts"] == 1
    assert row["status"] == "kept"


def test_book_insertion_and_hash_checks(store):
    """Books should be stored with their fingerprints."""
    data = b"sample academic book file"

    text = (
        "This is a sample academic mathematics textbook "
        "containing enough words to create useful fingerprints "
        "for testing the DocReaper storage system."
    )

    fp = fingerprint(data, text)

    book = Book(
        title="Sample Mathematics",
        authors="Test Author",
        year=2025,
        subject="Mathematics",
        source_url="https://example.org/math.pdf",
        fmt="pdf",
        size_bytes=len(data),
        license="public-domain",
        metadata={"test": True},
    )

    book_id = store.add_book(
        book,
        fp,
        "library/sample-mathematics.pdf",
    )

    assert book_id > 0
    assert store.count_books() == 1

    assert store.has_file_sha256(fp.file_sha256)
    assert store.has_text_sha256(fp.text_sha256)

    assert not store.has_file_sha256("unknown-file-hash")
    assert not store.has_text_sha256("unknown-text-hash")


def test_near_duplicate_detection(store):
    """Highly similar text fingerprints should be detected."""
    text = (
        "Computer science studies computation algorithms "
        "programming software hardware and information systems "
        "for modern digital applications and research."
    )

    data = b"computer science book"
    fp = fingerprint(data, text)

    book = Book(
        title="Computer Science Fundamentals",
        subject="Computer Science",
        size_bytes=len(data),
    )

    store.add_book(
        book,
        fp,
        "library/computer-science.pdf",
    )

    result = store.near_duplicate(
        shingles=fp.shingles,
        word_count=fp.word_count,
        threshold=0.80,
    )

    assert result is not None
    assert result["title"] == book.title
    assert result["score"] == 1.0


def test_worker_state_updates(store):
    """Worker state updates should be persisted."""
    store.update_worker_state(
        status="running",
        pause_requested=True,
        stop_requested=False,
        summary={"books_processed": 12},
    )

    state = store.get_worker_state()

    assert state["status"] == "running"
    assert state["pause_requested"] == 1
    assert state["stop_requested"] == 0
    assert state["summary"]["books_processed"] == 12


def test_run_history(store):
    """Runs should be created and updated correctly."""
    run_id = store.start_run()

    assert run_id > 0

    store.finish_run(
        run_id,
        status="completed",
        books_added=5,
        candidates_processed=20,
        summary={"source": "test"},
    )

    row = store.conn.execute(
        "SELECT * FROM run_history WHERE id = ?",
        (run_id,),
    ).fetchone()

    assert row["status"] == "completed"
    assert row["books_added"] == 5
    assert row["candidates_processed"] == 20
    assert row["finished_at"] is not None


def test_engine_logs(store):
    """Engine logs should be stored and retrievable."""
    log_id = store.add_log(
        "Discovery engine started.",
        level="INFO",
    )

    assert log_id > 0

    logs = store.recent_logs()

    assert len(logs) == 1
    assert logs[0]["message"] == "Discovery engine started."
    assert logs[0]["level"] == "INFO"
    assert logs[0]["id"] == log_id


def test_statistics(store):
    """Statistics should report accurate catalogue and queue counts."""
    record = DiscoveryRecord(
        url="https://example.org/statistics.pdf",
        title="Statistics Test",
    )

    store.add_discovery(record)

    stats = store.stats()

    assert stats["books"] == 0
    assert stats["discovery_pending"] == 1
    assert stats["discovery_kept"] == 0
    assert stats["discovery_failed"] == 0
