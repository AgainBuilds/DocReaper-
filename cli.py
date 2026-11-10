"""DocReaper command line interface."""
from __future__ import annotations

import asyncio
import json
import logging

import typer

from .cloud import CloudStore
from .config import settings
from .pipeline import Pipeline
from .storage import Store

app = typer.Typer(help="DocReaper: academic book discovery and deduplication")


@app.command()
def run(
    db: str = typer.Option(settings.database_url, help="SQLite database URL"),
    batch_size: int = typer.Option(50, help="Discovery queue batch size"),
    target: int = typer.Option(settings.target_unique_books, help="Book goal"),
) -> None:
    """Discover, download, deduplicate and store books until the target."""
    logging.basicConfig(level=settings.log_level)
    pipeline = Pipeline(Store(db), CloudStore(), target=target)
    summary = asyncio.run(pipeline.run(batch_size))
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def discover(
    db: str = typer.Option(settings.database_url, help="SQLite database URL"),
    pages: int = typer.Option(settings.pages_per_subject, help="Pages per subject"),
) -> None:
    """Fetch new book URLs into the discovery queue."""
    pipeline = Pipeline(Store(db), CloudStore())
    added = asyncio.run(pipeline.discover(pages))
    typer.echo(f"Added {added} new URLs to the queue")


@app.command()
def stats(db: str = typer.Option(settings.database_url, help="SQLite database URL")):
    """Show collection statistics."""
    typer.echo(json.dumps(Store(db).stats(), indent=2))


if __name__ == "__main__":
    app()
