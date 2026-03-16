from __future__ import annotations

import hashlib
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_CHUNK_SIZE = 1 << 16  # 64 KiB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def maybe_archive_previous(target: Path, new_sha: str) -> Path | None:
    """Rename *target* to ``{stem}__{old_sha[:8]}.zip`` if its SHA differs from *new_sha*.

    Returns the archive path when a rename happened, ``None`` otherwise.
    """
    if not target.exists():
        return None
    old_sha = sha256_file(target)
    if old_sha == new_sha:
        return None
    archive_name = f"{target.stem}__{old_sha[:8]}{target.suffix}"
    archive_path = target.with_name(archive_name)
    target.rename(archive_path)
    LOGGER.info("Archived previous bhavcopy %s → %s", target.name, archive_name)
    return archive_path
