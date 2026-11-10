from docreaper.cloud import CloudStore
from docreaper.pipeline import Pipeline
from docreaper.storage import Store

BASE = " ".join(f"word{i}" for i in range(80))


class LocalCloud(CloudStore):
    def __init__(self, tmp_path):
        super().__init__(bucket="", prefix="books", local_dir=str(tmp_path))


def test_ingest_keeps_unique_and_rejects_duplicates(tmp_path):
    store = Store("sqlite:///:memory:")
    pipe = Pipeline(store, LocalCloud(tmp_path))

    first = pipe.ingest_bytes(b"file-one", text=BASE, fmt="text", title="Book A")
    assert first["status"] == "kept"

    same_file = pipe.ingest_bytes(
        b"file-one", text=BASE, fmt="text", title="Book A again"
    )
    assert same_file["status"] == "duplicate"

    near = pipe.ingest_bytes(
        b"file-two", text=BASE + " a few new words", fmt="text", title="Book B"
    )
    assert near["status"] == "duplicate"

    other = pipe.ingest_bytes(
        b"file-three", text="utterly unrelated content " * 20,
        fmt="text", title="Book C",
    )
    assert other["status"] == "kept"
    assert store.count_books() == 2
