# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Streaming frame zips (Unreal sequence rendering FR-SL-2, D17).

A zip is written on the fly while the client reads it: ``zipfile`` writes
into :class:`_Sink`, an in-memory buffer without ``seek`` or ``tell``, so
it uses data descriptors and ZIP64 records where needed; after each chunk
of a frame (1 MiB) the buffer is handed to the response and emptied.  So
at most about one chunk is held in memory, and nothing is spooled to disk.
Entries are stored, not compressed (PNG and EXR are compressed already).

At most ``SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS`` (default 2) zips stream
at once (:data:`SLOTS`).  The view takes a slot (a :class:`Lease`) before
it builds the response; :class:`SlotStream` gives it back exactly once,
when the stream ends, fails, or is closed.  Django's
``StreamingHttpResponse.close()`` (run by the WSGI server when the response
finishes or the client disconnects) closes the stream, so a dropped
download frees its slot even when it never started.

A stalled client never pulls the next chunk, so the stream itself cannot
notice it.  A lease is therefore renewed by every chunk the server takes;
one idle for ``SETHLANS_ZIP_IDLE_SECONDS`` (default 300) is expired by the
next download that finds every slot busy: its slot is reused at once, and
the stalled stream ends at its next chunk.

A frame that cannot be read once streaming has started ends the stream
with an error (the client gets a broken download, never a zip quietly
missing a frame); the view checks every frame before it starts.

Bounded memory relies on the manager being served by Waitress (WSGI,
``sethlans_manager.wsgi``): the server pulls one chunk at a time from this
iterator and blocks a stalled client in its own output buffer.  Under ASGI
Django would wrap this synchronous iterator differently; a test pins the
WSGI serving path (``test_zip_stream.py``).
"""

import logging
import os
import threading
import time
import zipfile

from django.conf import settings

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024
DEFAULT_ZIP_DOWNLOADS = 2
DEFAULT_IDLE_SECONDS = 300


def _setting(name, default):
    return max(1, int(getattr(settings, name, default)))


class Lease:
    """One taken slot: renewed by every chunk, given back once."""

    def __init__(self, slots):
        self._slots = slots
        self.seen = time.monotonic()
        self.expired = False
        self.returned = False

    def renew(self):
        self.seen = time.monotonic()

    def give_back(self):
        self._slots.give_back(self)


class ZipSlots:
    """The running zip downloads, with the limit and idle time read from the settings at each take."""

    def __init__(self):
        self._lock = threading.Lock()
        self._leases = set()

    @staticmethod
    def limit():
        return _setting('SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS', DEFAULT_ZIP_DOWNLOADS)

    @staticmethod
    def idle_seconds():
        return _setting('SETHLANS_ZIP_IDLE_SECONDS', DEFAULT_IDLE_SECONDS)

    def _expire_idle(self):
        cutoff = time.monotonic() - self.idle_seconds()
        for lease in [lease for lease in self._leases if lease.seen < cutoff]:
            lease.expired = True
            self._leases.discard(lease)
            logger.warning("Zip download idle for over %s s: its slot is reused.", self.idle_seconds())

    def take(self):
        """Take a slot: a :class:`Lease`, or ``None`` when every slot is busy and none is idle."""
        with self._lock:
            if len(self._leases) >= self.limit():
                self._expire_idle()
            if len(self._leases) >= self.limit():
                return None
            lease = Lease(self)
            self._leases.add(lease)
            return lease

    def give_back(self, lease):
        with self._lock:
            lease.returned = True
            self._leases.discard(lease)

    @property
    def active(self):
        with self._lock:
            return len(self._leases)


SLOTS = ZipSlots()


class _Sink:
    """A write-only buffer for ``zipfile``: no ``seek``/``tell``, so the archive streams."""

    def __init__(self):
        self._parts = []

    def write(self, data):
        self._parts.append(bytes(data))
        return len(data)

    def flush(self):
        """Nothing to flush: :meth:`drain` hands the bytes out."""

    def drain(self):
        data = b''.join(self._parts)
        self._parts = []
        return data


def _info(arcname, path):
    stat = os.stat(path)
    stamp = time.localtime(stat.st_mtime)[:6]
    info = zipfile.ZipInfo(arcname, date_time=max(stamp, (1980, 1, 1, 0, 0, 0)))
    info.compress_type = zipfile.ZIP_STORED
    info.file_size = stat.st_size
    return info


def zip_chunks(entries, chunk=CHUNK):
    """Yield the bytes of a stored ZIP64 archive of ``entries`` (``(arcname, absolute path)``).

    Raises:
        OSError: a file is gone or unreadable when its turn comes (the stream ends broken).
    """
    sink = _Sink()
    with zipfile.ZipFile(sink, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for arcname, path in entries:
            info = _info(arcname, path)
            with open(path, 'rb') as handle, archive.open(info, 'w') as dest:
                while True:
                    data = handle.read(chunk)
                    if not data:
                        break
                    dest.write(data)
                    yield sink.drain()
            yield sink.drain()
    yield sink.drain()


class SlotStream:
    """An iterator over :func:`zip_chunks` that gives its lease back exactly once."""

    def __init__(self, entries, lease):
        self._chunks = zip_chunks(entries)
        self._lease = lease
        self._open = True

    def __iter__(self):
        return self

    def __next__(self):
        try:
            if self._lease.expired:
                logger.warning("Zip download ended: the client stalled and its slot was reused.")
                raise StopIteration
            while True:
                data = next(self._chunks)
                if data:
                    self._lease.renew()
                    return data
        except BaseException:
            self.close()
            raise

    def close(self):
        """Stop the archive and give the slot back (idempotent)."""
        if not self._open:
            return
        self._open = False
        try:
            self._chunks.close()
        finally:
            self._lease.give_back()
