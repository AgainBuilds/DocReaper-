# Changelog

## 2.0.0

Scope reset: DocReaper is an academic book discovery, deduplication and
cloud-storage system built as a six-step pipeline.

- Discovery targeted at undergraduate textbooks per subject, starting with
  Computer Science and Informatics, Mathematics, Physics, General Studies and
  Supporting Sciences; first milestone is 270 unique books.
- Mathematical core: SHA-256 file hashing, normalised-text hashing, and
  Jaccard similarity over 5-word shingles.
- Removed: FastAPI service, MinHash/LSH, SimHash, benchmarking harnesses,
  distributed-processing scaffolding and the phase-3 reconciliation layer.
- Rich per-book catalogue metadata (authors, year, subject, category,
  description, source URL, storage location, licensing status).
- Google Cloud Storage upload with a local-folder fallback; polite downloader
  with retries and size caps; SQLite catalogue with exact-hash indexes.
- Renamed package to `docreaper`, env prefix to `DOCREAPER_`, CLI entry point
  `docreaper`.
