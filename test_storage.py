from docreaper.fingerprints import fingerprint
from docreaper.schemas import Book, DiscoveryRecord
from docreaper.storage import Store

BASE = " ".join(f"word{i}" for i in range(60))


def make_store():
    return Store("sqlite:///:memory:")


def add(store, text, title="T"):
    fp = fingerprint(b"data-" + title.encode(), text)
    return store.add_book(Book(title, "A", subject="test"), fp, "gs://b/x")


def test_add_and_count():
    store = make_store()
    add(store, BASE)
    assert store.count_books() == 1


def test_exact_duplicate_hashes():
    store = make_store()
    fp = fingerprint(b"same", BASE)
    store.add_book(Book("A"), fp, "p")
    assert store.has_file_sha256(fp.file_sha256)
    assert store.has_text_sha256(fp.text_sha256)


def test_near_duplicate_found():
    store = make_store()
    add(store, BASE, "original")
    edited = BASE + " extra tail words that are new here"
    fp = fingerprint(b"z", edited)
    near = store.near_duplicate(fp.shingles, fp.word_count, 0.5)
    assert near is not None and near["score"] >= 0.5


def test_discovery_queue_deduplicates():
    store = make_store()
    assert store.add_discovery(DiscoveryRecord("http://a", "A", source="s"))
    assert not store.add_discovery(DiscoveryRecord("http://a", "A", source="s"))
    assert len(store.pending_discoveries(10)) == 1
    store.set_discovery_status("http://a", "kept")
    assert store.pending_discoveries(10) == []
