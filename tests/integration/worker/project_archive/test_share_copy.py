# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Copy stage (Unreal foundation FR-PA-4, FR-PA-5, FR-T-6 "Copy stage").
The share is a local file reached through ``share_validator=``.
"""
import io
import os
import threading
import time

import pytest

from sethlans_worker_agent.project_archive import eviction, share_copy
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import (
    AbortedError, ArchiveChangedError, ArchivePinMismatchError, ArchiveTooLargeError, ShareStallError,
)

from . import _helpers as h


def _copy(cache, source, pin, abort=None, **kwargs):
    options = dict(max_bytes=10 * h.GIB, stall_seconds=5.0, reserve=lambda n: eviction.reserve(cache, n),
                   share_validator=lambda p: str(p), poll_seconds=0.02)
    options.update(kwargs)
    return share_copy.copy_archive(str(source), cache.layout.downloads, pin, abort or Abort(), **options)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class BlockingReader(io.RawIOBase):
    """A share file whose first read blocks until ``release`` is set."""

    def __init__(self, data: bytes):
        self.release = threading.Event()
        self.entered = threading.Event()
        self._data = io.BytesIO(data)

    def readable(self):
        return True

    def read(self, size=-1):
        self.entered.set()
        self.release.wait(10)
        return self._data.read(size)


def test_copy_hashes_and_consumes_reservation(tmp_path):
    source = tmp_path / 'a.zip'
    pin = h.make_zip(source)
    cache = h.make_cache(tmp_path)
    copied = _copy(cache, source, pin, chunk=1024)
    assert copied.sha256 == pin and copied.size == source.stat().st_size
    assert copied.path.read_bytes() == source.read_bytes()
    assert copied.source.mtime_ns == source.stat().st_mtime_ns
    assert cache.ledger.in_flight_reserved == 0


def test_pin_mismatch_raises_and_removes_part(tmp_path):
    source = tmp_path / 'a.zip'
    h.make_zip(source)
    cache = h.make_cache(tmp_path)
    with pytest.raises(ArchivePinMismatchError, match=r'^Archive SHA-256 mismatch: expected 0000000000000000…'):
        _copy(cache, source, '0' * 64)
    assert list(cache.layout.downloads.iterdir()) == []
    assert cache.ledger.in_flight_reserved == 0


def test_max_archive_size_enforced_before_any_read(tmp_path):
    source = tmp_path / 'a.zip'
    pin = h.make_zip(source)
    cache = h.make_cache(tmp_path)

    def never_open(path):
        raise AssertionError("must not read")

    with pytest.raises(ArchiveTooLargeError):
        _copy(cache, source, pin, max_bytes=10, opener=never_open)
    assert cache.ledger.in_flight_reserved == 0


def test_growing_source_aborts_at_size_plus_one(tmp_path):
    source = tmp_path / 'a.zip'
    source.write_bytes(b'z' * 100)
    cache = h.make_cache(tmp_path)
    reads = []

    class Growing(io.BytesIO):
        def read(self, size=-1):
            data = super().read(size)
            reads.append(len(data))
            return data

    with pytest.raises(ArchiveChangedError, match='grew during copy'):
        _copy(cache, source, '0' * 64, chunk=1, opener=lambda p: Growing(b'z' * 101))
    assert sum(reads) == 101
    assert _wait_for(lambda: list(cache.layout.downloads.iterdir()) == [])


def test_source_changed_during_copy(tmp_path):
    source = tmp_path / 'a.zip'
    pin = h.make_zip(source)
    cache = h.make_cache(tmp_path)
    st = source.stat()

    def touching_open(path):
        handle = open(path, 'rb')
        os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        return handle

    with pytest.raises(ArchiveChangedError, match='changed during copy'):
        _copy(cache, source, pin, opener=touching_open)
    assert list(cache.layout.downloads.iterdir()) == []


def test_stall_returns_promptly_and_part_removed_later(tmp_path):
    source = tmp_path / 'a.zip'
    source.write_bytes(b'q' * 64)
    cache = h.make_cache(tmp_path)
    reader = BlockingReader(b'q' * 64)
    started = time.monotonic()
    with pytest.raises(ShareStallError):
        _copy(cache, source, '0' * 64, stall_seconds=0.3, opener=lambda p: reader)
    assert time.monotonic() - started < 3.0
    assert cache.ledger.in_flight_reserved == 0
    assert len(list(cache.layout.downloads.iterdir())) == 1  # the blocked read still owns it
    reader.release.set()
    assert _wait_for(lambda: list(cache.layout.downloads.iterdir()) == [])


def test_cancel_while_blocked_cleans_up(tmp_path):
    source = tmp_path / 'a.zip'
    source.write_bytes(b'q' * 64)
    cache = h.make_cache(tmp_path)
    reader = BlockingReader(b'q' * 64)
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(AbortedError) as caught:
            _copy(cache, source, '0' * 64, Abort(cancel=cancel), opener=lambda p: reader)
    finally:
        timer.join()
    assert caught.value.cause == 'canceled'
    reader.release.set()
    assert _wait_for(lambda: list(cache.layout.downloads.iterdir()) == [])


def test_shutdown_is_checked_directly(tmp_path):
    source = tmp_path / 'a.zip'
    source.write_bytes(b'q' * 64)
    cache = h.make_cache(tmp_path)
    reader = BlockingReader(b'q' * 64)
    shutdown = threading.Event()
    shutdown.set()
    with pytest.raises(AbortedError) as caught:
        _copy(cache, source, '0' * 64, Abort(shutdown=shutdown), opener=lambda p: reader)
    assert caught.value.cause == 'shutdown'
    reader.release.set()
    assert _wait_for(lambda: list(cache.layout.downloads.iterdir()) == [])


def test_default_share_validator_refuses_local_paths(tmp_path):
    source = tmp_path / 'a.zip'
    pin = h.make_zip(source)
    cache = h.make_cache(tmp_path)
    with pytest.raises(share_copy.ArchiveError, match='Invalid archive path'):
        share_copy.copy_archive(str(source), cache.layout.downloads, pin, Abort(), max_bytes=h.GIB,
                                stall_seconds=5, reserve=lambda n: eviction.reserve(cache, n))
