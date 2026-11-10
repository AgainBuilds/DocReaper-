# DocReaper

DocReaper is an automated academic book discovery, deduplication and
cloud-storage system. It finds open-access undergraduate textbooks online,
downloads them, applies mathematical duplicate detection, keeps only unique
books, uploads them to Google Cloud Storage, and catalogues everything in a
database.

## The pipeline

1. **Internet discovery** - Open Library / Internet Archive and Project
   Gutenberg, searched per subject (computer science, mathematics, physics,
   general studies, supporting sciences).
2. **Download and extraction** - polite HTTP downloads with retries and a
   size cap; text extraction from PDF, EPUB, DOCX, HTML and plain text.
3. **Mathematical analysis** - SHA-256 for identical files, a normalised-text
   hash for identical content in different files, and Jaccard similarity over
   5-word shingles for near duplicates.
4. **Unique-book filter** - exact and near duplicates are rejected, unique
   books are retained.
5. **Cloud storage** - unique files are uploaded to GCS (with a local
   `library/` fallback when no bucket is configured).
6. **Target** - the run stops at `DOCREAPER_TARGET_UNIQUE_BOOKS` unique books
   (default 270 for the first CampusCorrect milestone).

## Every kept book is catalogued with

title, authors, publication year, subject, category, description, source URL,
file location, format, size, and licensing status. Department, course mapping
and academic level are left to CampusCorrect, which decides relevance per
student course.

## Quick start

    python -m pip install -e ".[dev,gcs]"
    cp .env.example .env   # set DOCREAPER_GCS_BUCKET when ready
    docreaper discover     # fill the discovery queue
    docreaper run          # download, deduplicate, store until the target
    docreaper stats        # collection statistics

Only legally accessible materials (public-domain and open-access sources)
are collected. Set `DOCREAPER_TARGET_UNIQUE_BOOKS=100000` for the long-term
goal.
