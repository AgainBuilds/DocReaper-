"""Text extraction from downloaded book files."""
from __future__ import annotations

import io
import zipfile

from bs4 import BeautifulSoup


class ExtractionError(ValueError):
    pass


def _extract_pdf(data: bytes) -> str:
    import fitz  # PyMuPDF

    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _extract_epub(data: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith((".xhtml", ".html", ".htm")):
                soup = BeautifulSoup(zf.read(name), "lxml")
                parts.append(soup.get_text(" "))
    return "\n".join(parts)


def _extract_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


def _extract_html(data: bytes) -> str:
    return BeautifulSoup(data.decode("utf-8", "replace"), "lxml").get_text(" ")


def extract_bytes(data: bytes, content_type: str = "") -> tuple[str, str]:
    """Detect the format and return (text, fmt)."""
    if data.startswith(b"%PDF"):
        return _extract_pdf(data), "pdf"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
            if "word/document.xml" in names:
                return _extract_docx(data), "docx"
            if "mimetype" in names or any(n.endswith(".xhtml") for n in names):
                return _extract_epub(data), "epub"
        except zipfile.BadZipFile:
            pass
    head = data[:512].lstrip().lower()
    if b"<html" in head or b"<body" in head or "text/html" in content_type:
        return _extract_html(data), "html"
    return data.decode("utf-8", "replace"), "text"
