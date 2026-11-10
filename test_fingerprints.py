from docreaper.fingerprints import fingerprint, jaccard, normalize_text, text_hash


def test_normalisation_invariance():
    a = "Hello   World\nCafe"
    b = "  hello world CAFE  "
    assert normalize_text(a) == normalize_text(b)
    assert text_hash(a) == text_hash(b)


def test_identical_content_different_files_hash_equal():
    fp1 = fingerprint(b"file-one", "one two three four five six seven")
    fp2 = fingerprint(b"file-two", "one two three four five six seven")
    assert fp1.text_sha256 == fp2.text_sha256
    assert fp1.file_sha256 != fp2.file_sha256


def test_jaccard_identical_and_different():
    fp1 = fingerprint(b"1", "one two three four five six seven")
    fp2 = fingerprint(b"2", "one two three four five six seven")
    fp3 = fingerprint(b"3", "completely different words appear here now")
    assert jaccard(fp1.shingles, fp2.shingles) == 1.0
    assert jaccard(fp1.shingles, fp3.shingles) < 0.5


def test_empty_text():
    fp = fingerprint(b"x", "")
    assert fp.shingles == frozenset()
    assert jaccard(fp.shingles, fp.shingles) is None
