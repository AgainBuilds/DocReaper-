"""Shared data records."""
from dataclasses import dataclass, field


@dataclass
class DiscoveryRecord:
    """A downloadable book URL found by a discovery source."""

    url: str
    title: str
    authors: str = ""
    source: str = ""
    subject: str = ""
    category: str = ""
    year: int | None = None
    description: str = ""


@dataclass
class Book:
    """A unique book kept in the library."""

    title: str
    authors: str = ""
    year: int | None = None
    edition: str = ""
    subject: str = ""
    department: str = ""
    courses: str = ""
    level: str = ""
    description: str = ""
    source_url: str = ""
    fmt: str = "pdf"
    size_bytes: int = 0
    license: str = "unknown"
    metadata: dict = field(default_factory=dict)
