# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``workers.services.secret_key.load_or_create_secret_key``.

Covers the first-boot generate, second-boot read, atomic write, and
failure modes described in Issue #63.
"""

import os
import platform
import stat
import time

import pytest
from django.core.exceptions import ImproperlyConfigured

from workers.services.secret_key import (
    SECRET_KEY_FILENAME,
    load_or_create_secret_key,
)


def test_first_boot_creates_key_file(tmp_path):
    """Empty data dir: generates a fresh key, persists it, returns it."""
    key = load_or_create_secret_key(tmp_path)

    key_file = tmp_path / SECRET_KEY_FILENAME
    assert key_file.exists()
    on_disk = key_file.read_text(encoding="utf-8").strip()
    assert on_disk == key
    assert len(key) >= 50

    if platform.system() != "Windows":
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600


def test_second_boot_reads_persisted_key(tmp_path):
    """Pre-existing key file: return the same key, do not rewrite."""
    key_file = tmp_path / SECRET_KEY_FILENAME
    known = (
        "known-test-secret-key-with-enough-entropy-for-django-checks-0123456789"
    )
    key_file.write_text(known, encoding="utf-8")
    original_mtime = key_file.stat().st_mtime

    # Sleep briefly so a rewrite would change mtime detectably.
    time.sleep(0.05)

    returned = load_or_create_secret_key(tmp_path)

    assert returned == known
    assert key_file.stat().st_mtime == original_mtime


def test_atomic_write_no_tmp_leftover(tmp_path):
    """After a successful first-boot write, no .partial sibling remains."""
    load_or_create_secret_key(tmp_path)

    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name != SECRET_KEY_FILENAME
    ]
    assert leftovers == [], f"Unexpected files in data dir: {leftovers}"


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="POSIX-only: chmod 0o500 not meaningful on Windows.",
)
def test_raises_on_unwritable_dir(tmp_path):
    """Read-only data dir: raises ImproperlyConfigured; no file created."""
    # Create a subdir we can lock down without affecting tmp_path cleanup.
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(str(locked), 0o500)  # r-x, no write

    try:
        with pytest.raises(ImproperlyConfigured) as exc_info:
            load_or_create_secret_key(locked)
        assert str(locked) in str(exc_info.value) or SECRET_KEY_FILENAME in str(
            exc_info.value
        )
        assert not (locked / SECRET_KEY_FILENAME).exists()
    finally:
        # Restore perms so pytest can clean up.
        os.chmod(str(locked), 0o700)


def test_empty_persisted_file_regenerates(tmp_path):
    """An empty ``secret_key`` file is replaced with a freshly generated one."""
    key_file = tmp_path / SECRET_KEY_FILENAME
    key_file.write_text("", encoding="utf-8")

    returned = load_or_create_secret_key(tmp_path)

    on_disk = key_file.read_text(encoding="utf-8").strip()
    assert on_disk, "Empty file should have been replaced"
    assert on_disk == returned
    assert len(returned) >= 50
