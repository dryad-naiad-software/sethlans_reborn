# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the streaming frame zip (Unreal sequence spec FR-SL-2, D17,
AC-20, AC-39).

Covers: the stream is a valid stored zip with the given entry names and
bytes; it is produced in pieces of about one chunk, never as one block; a
file gone before its turn ends the stream with an error; the slots refuse
above the limit, read the limit and idle time from the settings, and expire
an idle lease only when every slot is busy; :class:`SlotStream` gives its
slot back exactly once when it ends, when it is closed before starting or
part way, and when the archive fails, and an expired stream stops at its
next chunk; the manager is served through the WSGI application and the zip
response is a synchronous stream.
"""

import io
import zipfile
from unittest import mock

import pytest

from workers.views import zip_stream
from workers.views.zip_stream import SlotStream, ZipSlots, zip_chunks


@pytest.fixture
def frames(tmp_path):
    paths = []
    for number in range(3):
        path = tmp_path / f'{number:07d}.png'
        path.write_bytes(bytes([number]) * (3000 + number))
        paths.append((f'0001_sh010/sh010.{number:07d}.png', str(path)))
    return paths


def _read(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {info.filename: (info.compress_type, archive.read(info)) for info in archive.infolist()}


class TestChunks:

    def test_valid_stored_zip(self, frames):
        entries = _read(b''.join(zip_chunks(frames)))
        assert list(entries) == [name for name, _path in frames]
        assert entries['0001_sh010/sh010.0000002.png'] == (zipfile.ZIP_STORED, b'' * 3002)

    def test_streams_in_small_pieces(self, frames):
        pieces = [piece for piece in zip_chunks(frames, chunk=1024) if piece]
        assert len(pieces) >= 9 and max(len(piece) for piece in pieces) <= 1024 + 200

    def test_file_gone_mid_stream_ends_the_stream_with_an_error(self, frames, tmp_path):
        """Review item 4: never a zip quietly missing a frame."""
        gone = [('gone.png', str(tmp_path / 'gone.png'))]
        with pytest.raises(OSError):
            b''.join(zip_chunks(frames + gone))

    def test_empty_archive_is_valid(self):
        assert _read(b''.join(zip_chunks([]))) == {}


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    fake = _Clock()
    with mock.patch.object(zip_stream.time, 'monotonic', fake):
        yield fake


class TestSlots:

    def test_limit_from_settings(self, settings):
        settings.SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS = 2
        slots = ZipSlots()
        first, second = slots.take(), slots.take()
        assert first and second and slots.take() is None
        first.give_back()
        assert slots.take() is not None and slots.active == 2

    def test_defaults(self, settings):
        del settings.SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS
        assert ZipSlots.limit() == zip_stream.DEFAULT_ZIP_DOWNLOADS == 2
        assert ZipSlots.idle_seconds() == zip_stream.DEFAULT_IDLE_SECONDS == 300

    def test_give_back_twice_is_harmless(self):
        slots = ZipSlots()
        lease = slots.take()
        lease.give_back()
        lease.give_back()
        assert slots.active == 0

    def test_idle_lease_is_expired_only_when_slots_are_busy(self, settings, clock):
        """Review item 3: two stalled clients cannot block every zip download."""
        settings.SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS = 2
        settings.SETHLANS_ZIP_IDLE_SECONDS = 60
        slots = ZipSlots()
        stalled, busy = slots.take(), slots.take()
        clock.now += 59
        busy.renew()
        assert slots.take() is None
        clock.now += 2
        fresh = slots.take()
        assert fresh is not None and stalled.expired and not busy.expired
        assert slots.active == 2


def _taken(slots=None):
    slots = slots or ZipSlots()
    lease = slots.take()
    assert lease
    return slots, lease


class TestSlotStream:

    def test_slot_freed_when_the_stream_ends(self, frames):
        slots, lease = _taken()
        stream = SlotStream(frames, lease)
        assert _read(b''.join(stream))
        assert slots.active == 0
        stream.close()
        assert slots.active == 0

    def test_close_before_the_first_chunk(self, frames):
        slots, lease = _taken()
        SlotStream(frames, lease).close()
        assert slots.active == 0

    def test_close_part_way_frees_once(self, frames):
        slots, lease = _taken()
        stream = SlotStream(frames, lease)
        assert next(stream)
        stream.close()
        stream.close()
        assert slots.active == 0

    def test_failure_frees_the_slot(self, frames):
        slots, lease = _taken()
        stream = SlotStream(frames, lease)
        with mock.patch.object(zip_stream, '_info', side_effect=RuntimeError('disk')):
            with pytest.raises(RuntimeError):
                list(stream)
        assert slots.active == 0

    def test_expired_stream_stops_at_its_next_chunk(self, frames, settings, clock):
        settings.SETHLANS_MAX_CONCURRENT_ZIP_DOWNLOADS = 1
        settings.SETHLANS_ZIP_IDLE_SECONDS = 10
        slots, lease = _taken()
        stream = SlotStream(frames, lease)
        assert next(stream)
        clock.now += 11
        replacement = slots.take()
        assert replacement is not None
        with pytest.raises(StopIteration):
            next(stream)
        assert slots.active == 1
        replacement.give_back()
        assert slots.active == 0


class TestServedByWsgi:
    """Review item 5: bounded memory needs the WSGI server to pull the iterator chunk by chunk."""

    def test_manager_serves_the_wsgi_application(self):
        import inspect

        from sethlans_manager import waitress_launcher, wsgi
        source = inspect.getsource(waitress_launcher)
        assert 'from sethlans_manager.wsgi import application as wsgi_app' in source
        assert callable(wsgi.application) and not inspect.iscoroutinefunction(wsgi.application)

    def test_the_zip_response_is_a_synchronous_stream(self, frames):
        from django.http import StreamingHttpResponse
        slots, lease = _taken()
        stream = SlotStream(frames, lease)
        response = StreamingHttpResponse(stream, content_type='application/zip')
        assert response.is_async is False
        assert stream.close in response._resource_closers  # the server's close() reaches the stream
        stream.close()
        assert slots.active == 0
