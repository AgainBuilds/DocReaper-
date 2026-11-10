import io

from docreaper.extraction import extract_bytes


def test_html():
    text, fmt = extract_bytes(
        b"<html><body><p>Hello book</p></body></html>", "text/html"
    )
    assert fmt == "html"
    assert "Hello book" in text


def test_plain_text():
    text, fmt = extract_bytes(b"A simple plain text book.", "text/plain")
    assert fmt == "text"
    assert "plain text book" in text


def test_docx():
    import docx

    buf = io.BytesIO()
    docx.Document().save(buf)
    text, fmt = extract_bytes(buf.getvalue(), "")
    assert fmt == "docx"
