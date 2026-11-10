"""Content fingerprinting: SHA-256, normalised text hash and Jaccard."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

WORD_RE = re.compile(r"\w+", re.UNICODE)
SHINGLE_SIZE = 5


def normalize_text(text: str) -> str:
    """NFKC-normalise, casefold and collapse all whitespace."""
    return " ".join(WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    """Hash of the normalised text: identical content, different files."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _words(text: str) -> list[str]:
    return WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold())


def _shingle_hashes(words: list[str], k: int) -> frozenset[int]:
    if not words:
        return frozenset()
    if len(words) <= k:
        joined = " ".join(words).encode("utf-8")
        digest = hashlib.blake2b(joined, digest_size=8).digest()
        return frozenset({int.from_bytes(digest, "big")})
    hashes = set()
    for i in range(len(words) - k + 1):
        joined = " ".join(words[i : i + k]).encode("utf-8")
        hashes.add(
            int.from_bytes(hashlib.blake2b(joined, digest_size=8).digest(), "big")
        )
    return frozenset(hashes)


def jaccard(a: frozenset[int], b: frozenset[int]) -> float | None:
    """Jaccard similarity of two shingle sets. None if both are empty."""
    if not a and not b:
        return None
    union = len(a | b)
    return len(a & b) / union if union else None


@dataclass
class Fingerprint:
    file_sha256: str
    text_sha256: str
    shingles: frozenset[int]
    word_count: int

    def pack_shingles(self) -> bytes:
        return b"".join(v.to_bytes(8, "big") for v in sorted(self.shingles))

    @staticmethod
    def unpack_shingles(blob: bytes) -> frozenset[int]:
        return frozenset(
            int.from_bytes(blob[i : i + 8], "big") for i in range(0, len(blob), 8)
        )


def fingerprint(data: bytes, text: str) -> Fingerprint:
    words = _words(text)
    return Fingerprint(
        file_sha256=sha256_bytes(data),
        text_sha256=text_hash(text),
        shingles=_shingle_hashes(words, SHINGLE_SIZE),
        word_count=len(words),
    )
