from __future__ import annotations

import hashlib
from pathlib import Path

from sepa_scanner.utils.hashing import maybe_archive_previous, sha256_bytes, sha256_file


def test_sha256_file(tmp_path: Path) -> None:
    content = b"hello world"
    target = tmp_path / "test.bin"
    target.write_bytes(content)
    assert sha256_file(target) == hashlib.sha256(content).hexdigest()


def test_sha256_bytes() -> None:
    data = b"test data"
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_maybe_archive_previous_no_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "2026-03-07.zip"
    result = maybe_archive_previous(target, "abc123")
    assert result is None


def test_maybe_archive_previous_same_hash(tmp_path: Path) -> None:
    target = tmp_path / "2026-03-07.zip"
    content = b"same content"
    target.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    result = maybe_archive_previous(target, sha)
    assert result is None
    assert target.exists()


def test_maybe_archive_previous_different_hash(tmp_path: Path) -> None:
    target = tmp_path / "2026-03-07.zip"
    old_content = b"old content"
    target.write_bytes(old_content)
    old_sha = hashlib.sha256(old_content).hexdigest()
    new_sha = hashlib.sha256(b"new content").hexdigest()

    result = maybe_archive_previous(target, new_sha)
    assert result is not None
    assert result.name == f"2026-03-07__{old_sha[:8]}.zip"
    assert result.exists()
    assert not target.exists()
    assert result.read_bytes() == old_content
