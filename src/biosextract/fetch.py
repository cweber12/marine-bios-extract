"""Cached, validated archive download.

Downloads land in ``.cache/<key>/`` and are reused on every later run, so a
re-extraction with a different bounding box costs no network at all and keeps
working when the publisher is down. CDFW's map servers returned HTTP 500 during
development, which is exactly the situation the cache exists for.

Every download is validated before it is trusted. kelp-density-extract once
committed a 155-byte authorization error saved under a data filename; the
equivalent failure here is an HTML "service unavailable" page written to
``ds582.zip``. Status, magic bytes, declared length and archive openability are
all checked, and a file that fails any of them is deleted rather than cached.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .catalog import ResolvedSource, user_agent

#: Local ZIP magic. The first four bytes of every well-formed archive.
ZIP_MAGIC = b"PK\x03\x04"

#: Anything smaller than this is a status page, not a dataset.
MIN_PLAUSIBLE_BYTES = 512

#: Refuse downloads above this unless explicitly raised. The BIOS library holds
#: single archives of 150 MB and more, and "extract everything" over a wide box
#: is an easy way to pull several gigabytes by accident.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class FetchError(RuntimeError):
    """Raised when an archive cannot be downloaded or fails validation."""


class ArchiveTooLarge(FetchError):
    """The publisher's archive exceeds the configured ceiling."""


@dataclass
class CachedArchive:
    """A validated archive on disk, plus how it got there."""

    path: Path
    source: ResolvedSource
    sha256: str
    bytes: int
    from_cache: bool

    def as_dict(self) -> dict:
        d = self.source.as_dict()
        d.update(
            {
                "cached_path": str(self.path),
                "sha256": self.sha256,
                "downloaded_bytes": self.bytes,
                "from_cache": self.from_cache,
            }
        )
        return d


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".meta.json")


def _validate_zip(path: Path) -> None:
    """Raise unless ``path`` is a plausible, openable ZIP archive."""
    size = path.stat().st_size
    if size < MIN_PLAUSIBLE_BYTES:
        head = path.read_bytes()[:200]
        raise FetchError(
            "downloaded file is %d bytes, too small to be a dataset. "
            "It usually means the server returned an error page. First bytes:\n%r"
            % (size, head)
        )
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic != ZIP_MAGIC:
        raise FetchError(
            "downloaded file does not start with the ZIP magic %r but with %r. "
            "The publisher probably served an HTML error or a login page."
            % (ZIP_MAGIC, magic)
        )
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise FetchError(f"archive is corrupt; first bad member is {bad!r}")
            if not zf.namelist():
                raise FetchError("archive opened but contains no members")
    except zipfile.BadZipFile as exc:
        raise FetchError(f"archive will not open as a ZIP: {exc}") from exc


def fetch(
    source: ResolvedSource,
    cache_dir: Path,
    refresh: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: int = 600,
    verbose: bool = True,
) -> CachedArchive:
    """Download ``source`` into the cache, or reuse a valid cached copy."""
    cache_dir = Path(cache_dir)
    target_dir = cache_dir / source.dataset.key
    target_dir.mkdir(parents=True, exist_ok=True)
    name = source.url.rsplit("/", 1)[-1] or f"{source.dataset.key}.zip"
    path = target_dir / name
    meta_path = _sidecar(path)

    if path.exists() and not refresh:
        try:
            _validate_zip(path)
            digest = sha256_file(path)
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (OSError, ValueError):
                    meta = {}
            # Report an upstream revision instead of absorbing it silently.
            if (
                verbose
                and source.last_modified
                and meta.get("last_modified")
                and meta["last_modified"] != source.last_modified
            ):
                print(
                    "  note: %s changed upstream (cached %s, now %s). "
                    "Re-run with --refresh to pick it up."
                    % (source.dataset.key, meta["last_modified"], source.last_modified)
                )
            if verbose:
                print(
                    "  %s: using cached %s (%.1f MB)"
                    % (source.dataset.key, name, path.stat().st_size / 1e6)
                )
            return CachedArchive(
                path=path,
                source=source,
                sha256=digest,
                bytes=path.stat().st_size,
                from_cache=True,
            )
        except FetchError:
            # A bad cached file is worse than none. Drop it and re-download.
            path.unlink(missing_ok=True)

    if source.bytes is not None and source.bytes > max_bytes:
        raise ArchiveTooLarge(
            "%s is %.1f MB, above the %.1f MB ceiling. Raise it with "
            "--max-download-mb if that is genuinely what you want."
            % (source.dataset.key, source.bytes / 1e6, max_bytes / 1e6)
        )

    tmp = path.with_suffix(path.suffix + ".part")
    tmp.unlink(missing_ok=True)
    if verbose:
        size_note = f" ({source.bytes / 1e6:.1f} MB)" if source.bytes else ""
        print(f"  {source.dataset.key}: downloading {source.url}{size_note}", flush=True)

    req = urllib.request.Request(source.url, headers={"User-Agent": user_agent()})
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise FetchError(f"HTTP {resp.status} from {source.url}")
            with open(tmp, "wb") as out:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise ArchiveTooLarge(
                            "%s exceeded the %.1f MB ceiling mid-download"
                            % (source.dataset.key, max_bytes / 1e6)
                        )
                    out.write(block)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"HTTP {exc.code} fetching {source.url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"could not reach {source.url}: {exc.reason}") from exc
    except ArchiveTooLarge:
        tmp.unlink(missing_ok=True)
        raise

    # Trust the archive only after it survives every check.
    try:
        _validate_zip(tmp)
    except FetchError:
        tmp.unlink(missing_ok=True)
        raise

    if source.bytes is not None and written != source.bytes:
        tmp.unlink(missing_ok=True)
        raise FetchError(
            "%s: expected %d bytes from Content-Length but received %d. "
            "The transfer was truncated."
            % (source.dataset.key, source.bytes, written)
        )

    tmp.replace(path)
    digest = sha256_file(path)
    meta_path.write_text(
        json.dumps(
            {
                "url": source.url,
                "sha256": digest,
                "bytes": written,
                "last_modified": source.last_modified,
                "etag": source.etag,
                "resolved_at": source.resolved_at,
            },
            indent=2,
        )
    )
    if verbose:
        print(f"  {source.dataset.key}: {written / 1e6:.1f} MB cached, sha256 {digest[:12]}")
    return CachedArchive(
        path=path, source=source, sha256=digest, bytes=written, from_cache=False
    )


def adopt_local(source: ResolvedSource, archive: Path, cache_dir: Path,
                verbose: bool = True) -> CachedArchive:
    """Take a manually downloaded archive into the cache.

    The path for publishers like PMEP that gate their bulk download behind a
    registration form. The file is validated exactly as a fetched one would be,
    so a half-downloaded browser file fails here rather than three stages later.
    """
    archive = Path(archive)
    if not archive.exists():
        raise FetchError(f"local archive not found: {archive}")
    _validate_zip(archive)

    target_dir = Path(cache_dir) / source.dataset.key
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / archive.name
    if archive.resolve() != path.resolve():
        shutil.copy2(archive, path)
    digest = sha256_file(path)
    _sidecar(path).write_text(
        json.dumps(
            {
                "url": f"local:{archive}",
                "sha256": digest,
                "bytes": path.stat().st_size,
                "supplied_by": "--local-archive",
            },
            indent=2,
        )
    )
    if verbose:
        print(f"  {source.dataset.key}: adopted {archive.name} ({path.stat().st_size / 1e6:.1f} MB)")
    return CachedArchive(
        path=path,
        source=source,
        sha256=digest,
        bytes=path.stat().st_size,
        from_cache=False,
    )
