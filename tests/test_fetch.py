"""Download validation and caching.

kelp-density-extract once committed a 155-byte authorization error saved under a
data filename. These tests are the equivalent guard here: an HTML error page
written to ds582.zip must be rejected at the door, not three stages later.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biosextract import catalog
from biosextract import fetch as fetch_mod


def _source(key: str = "mpa"):
    return catalog.ResolvedSource(dataset=catalog.get(key), url="https://example/ds582.zip")


def test_html_error_page_is_rejected(tmp_path):
    bad = tmp_path / "ds582.zip"
    bad.write_bytes(b"<html><body>Service Unavailable</body></html>" * 40)
    with pytest.raises(fetch_mod.FetchError, match="ZIP magic"):
        fetch_mod._validate_zip(bad)


def test_tiny_file_is_rejected_with_its_contents_shown(tmp_path):
    bad = tmp_path / "ds582.zip"
    bad.write_bytes(b"403 Forbidden")
    with pytest.raises(fetch_mod.FetchError) as exc:
        fetch_mod._validate_zip(bad)
    assert "403 Forbidden" in str(exc.value)


def test_truncated_zip_is_rejected(tmp_path, archive):
    truncated = tmp_path / "cut.zip"
    truncated.write_bytes(archive.read_bytes()[: len(archive.read_bytes()) // 2])
    with pytest.raises(fetch_mod.FetchError):
        fetch_mod._validate_zip(truncated)


def test_good_archive_validates(archive):
    fetch_mod._validate_zip(archive)  # must not raise


def test_adopt_local_copies_and_hashes(tmp_path, archive):
    cached = fetch_mod.adopt_local(_source(), archive, tmp_path / "cache", verbose=False)
    assert cached.path.exists()
    assert cached.sha256 == fetch_mod.sha256_file(archive)
    assert cached.bytes == archive.stat().st_size
    assert Path(str(cached.path) + ".meta.json").exists()


def test_adopt_local_rejects_a_missing_file(tmp_path):
    with pytest.raises(fetch_mod.FetchError, match="not found"):
        fetch_mod.adopt_local(_source(), tmp_path / "nope.zip", tmp_path / "cache")


def test_adopt_local_rejects_a_half_downloaded_file(tmp_path, archive):
    partial = tmp_path / "partial.zip"
    partial.write_bytes(archive.read_bytes()[:100])
    with pytest.raises(fetch_mod.FetchError):
        fetch_mod.adopt_local(_source(), partial, tmp_path / "cache")


def test_oversize_archive_refused_before_download(tmp_path):
    src = _source()
    src.bytes = 900 * 1024 * 1024
    with pytest.raises(fetch_mod.ArchiveTooLarge, match="ceiling"):
        fetch_mod.fetch(src, tmp_path / "cache", max_bytes=512 * 1024 * 1024, verbose=False)


def test_cached_copy_is_reused_without_network(tmp_path, archive):
    cache = tmp_path / "cache"
    src = _source()
    first = fetch_mod.adopt_local(src, archive, cache, verbose=False)
    # fetch() must find the cached file and never touch the (bogus) URL.
    second = fetch_mod.fetch(src, cache, verbose=False)
    assert second.from_cache is True
    assert second.sha256 == first.sha256


def test_corrupt_cached_file_is_discarded(tmp_path, archive, monkeypatch):
    cache = tmp_path / "cache"
    src = _source()
    fetch_mod.adopt_local(src, archive, cache, verbose=False)
    (cache / "mpa" / "ds582.zip").write_bytes(b"<html>error</html>")

    # With the cache poisoned, fetch() must try the network rather than trust it.
    called = {}

    def _boom(*a, **k):
        called["yes"] = True
        raise fetch_mod.FetchError("network reached")

    monkeypatch.setattr(fetch_mod.urllib.request, "urlopen", _boom)
    with pytest.raises(fetch_mod.FetchError):
        fetch_mod.fetch(src, cache, verbose=False)
    assert called.get("yes"), "a bad cached file must not be served as valid"
