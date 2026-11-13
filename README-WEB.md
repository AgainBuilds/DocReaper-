
# DocReaper Control Center

The web dashboard for DocReaper.

## Features

- Collection statistics
- Target progress tracking
- Discovery queue statistics
- Duplicate and failure counts
- Recently catalogued books
- Pipeline architecture overview
- Health endpoint

## Installation

Install the existing DocReaper package:

    python -m pip install -e .

Install web dependencies:

    python -m pip install -r requirements-web.txt

## Run locally

    uvicorn web:app --reload

Open:

    http://127.0.0.1:8000

## API endpoints

- GET /health
- GET /api/overview
- GET /api/books
- GET /docs

## Database

The dashboard reads the database configured through:

    DOCREAPER_DATABASE_URL

The initial implementation uses the existing SQLite catalogue.

## Important

This dashboard is read-only.

It does not start discovery, download books, or modify collection records.

The default SQLite database is local storage. A free hosted web service
may lose local database changes when its instance is replaced or restarted.
Use persistent storage or a suitable external database before relying on
a hosted catalogue for permanent records.
