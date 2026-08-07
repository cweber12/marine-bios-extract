"""Provenance manifest.

Written on every run, in the same shape as kelp-density-extract's manifest so
the two can be read side by side. It answers the only question that matters when
an output is six months old: exactly which published archive produced this, and
would the same command produce it again today.

BIOS archives carry no version number in their URL, so the pin is the
combination of resolved URL, ``Last-Modified``, ``Content-Length`` and the
sha256 of the bytes actually received. If the publisher revises a dataset, the
hash moves and the difference is visible rather than absorbed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def git_commit(repo_root: Path | None = None) -> str | None:
    """Current commit, or None outside a checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root or Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def describe_output(path: Path, kind: str, description: str = "") -> dict:
    path = Path(path)
    return {
        "path": path.name,
        "kind": kind,
        "description": description,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build(
    bbox,
    request: dict,
    layers: list[dict],
    outputs: list[dict],
    warnings: list[str] | None = None,
) -> dict:
    """Assemble the manifest document."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {
            "name": "marine-bios-extract",
            "version": __version__,
            "git_commit": git_commit(),
        },
        "request": {
            "bbox": list(bbox.as_tuple()),
            "bbox_km": [round(bbox.width_km, 3), round(bbox.height_km, 3)],
            **request,
        },
        "layers": layers,
        "outputs": outputs,
        "warnings": warnings or [],
    }


def write(document: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path
