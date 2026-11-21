# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager calls of sequence rendering's worker results (FR-SD-8, FR-SW-10,
FR-SA-6). Every call has the pilot result's response
handling (``api_extras``): 200 -> ``done``; 409/404 -> ``terminal`` (the
job is no longer this worker's); 401/403 -> ``failed`` at once; 5xx or a
network error -> retried, 3 attempts with backoff, then ``failed``; any
other 4xx -> ``rejected`` with the response's error text. None raises.

* ``report_shots_result(job_id, body)``: ``POST jobs/{id}/shots-result/``.
  The body is at most about 300 KB (2,000 shots of 64-character names, 64
  warnings), well inside the endpoint's 1 MiB class (D21).
* ``upload_frame(job_id, frame, handle, filename)``: ``POST
  jobs/{id}/frames/`` (multipart ``frame``, ``file``) from the handle
  ``fs_safe.open_output_file`` returned: the checked bytes are read once
  (at most 512 MiB, the endpoint's file cap) and every attempt sends them.
* ``report_piece_result(job_id, body)``: ``POST jobs/{id}/piece-result/``.
* ``upload_sound(job_id, handle, offset, skipped, render_s, summary)``:
  ``POST jobs/{id}/sound-result/`` (multipart ``file`` from the handle
  ``fs_safe.open_output_file`` returned, at most 4 GiB, with the
  ``shared.unreal.sound_result`` fields). The body is **streamed** from the
  handle in blocks with its ``Content-Length`` (``_Multipart``); every
  attempt starts again from the file's first byte. A file over 4 GiB, or
  one that shrinks while it is sent, is ``rejected`` (never sent again).
"""
import logging
import os
import time
import uuid
from typing import Callable

import requests

from sethlans_worker_agent import api_extras
from shared.unreal.sound_result import MAX_SECONDS, SUMMARY_MAX_BYTES, offset_text

logger = logging.getLogger(__name__)

SHOTS_RESULT, PIECE_RESULT, FRAMES, SOUND_RESULT = 'shots-result', 'piece-result', 'frames', 'sound-result'
MAX_SOUND_BYTES = 4 * 1024 ** 3  # the manager's SOUND_FILE_CAP (D21)
SOUND_TIMEOUT_SECONDS = (15, 900)  # connect; then the manager re-reads the whole .wav before it answers
MAX_FRAME_BYTES = 512 * 1024 * 1024  # the manager's FRAME_FILE_CAP (D21)
FRAME_TIMEOUT_SECONDS = 120
_MIME = {'.png': 'image/png', '.exr': 'image/x-exr'}


def report_shots_result(job_id: int, body: dict,
                        sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    return api_extras.post_result(job_id, SHOTS_RESULT, body, sleep)


def report_piece_result(job_id: int, body: dict,
                        sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    return api_extras.post_result(job_id, PIECE_RESULT, body, sleep)


def _read(handle):
    """The checked bytes, or a ``PilotReport`` when they cannot be sent."""
    try:
        handle.seek(0)
        data = handle.read(MAX_FRAME_BYTES + 1)
    except OSError as exc:
        return api_extras.PilotReport('rejected', None, f"the frame could not be read ({type(exc).__name__})")
    if len(data) > MAX_FRAME_BYTES:
        return api_extras.PilotReport('rejected', 413, f"the frame is larger than {MAX_FRAME_BYTES} bytes")
    return data


def upload_frame(job_id: int, frame: int, handle, filename: str,
                 sleep: Callable[[float], None] = time.sleep) -> api_extras.PilotReport:
    """FR-SW-10: one kept frame to ``frames/``. Never raises."""
    data = _read(handle)
    if isinstance(data, api_extras.PilotReport):
        return data
    suffix = filename[filename.rfind('.'):].lower() if '.' in filename else ''
    files = {'file': (filename, data, _MIME.get(suffix, 'application/octet-stream'))}
    last = api_extras.PilotReport('failed')
    for attempt in range(api_extras.PILOT_ATTEMPTS):
        try:
            response = api_extras.get_session().request(
                'post', api_extras._url(f"jobs/{int(job_id)}/{FRAMES}/"), data={'frame': str(int(frame))},
                files=files, headers=api_extras.get_auth_headers(), timeout=FRAME_TIMEOUT_SECONDS)
            report = api_extras._classify(response)
            if report is not None:
                return report
            last = api_extras.PilotReport('failed', response.status_code, api_extras._detail(response))
        except requests.exceptions.RequestException as exc:
            last = api_extras.PilotReport('failed', None, f"{type(exc).__name__}: {exc}"[:api_extras.MAX_DETAIL])
        if attempt + 1 < api_extras.PILOT_ATTEMPTS:
            logger.warning("frames/ %s of job %s failed (%s); retrying.", frame, job_id, last.detail or last.status_code)
            sleep(api_extras.PILOT_BACKOFF_SECONDS[min(attempt, len(api_extras.PILOT_BACKOFF_SECONDS) - 1)])
    return last


class UploadStopped(Exception):
    """A shutdown, cancel or reassignment arrived while the sound was being sent."""


class _Multipart:
    """A ``multipart/form-data`` body read in blocks: the text fields, then the file from ``handle``, then the end.

    ``requests`` sends an object with ``read`` and ``__len__`` as a streamed body with that ``Content-Length``,
    so the file is never held in memory. ``cause`` (``Delivery.cause``) is checked at every block: a non-empty
    answer stops the send (``UploadStopped``)."""

    def __init__(self, fields, handle, size: int, cause: Callable[[], str] = lambda: ''):
        boundary = uuid.uuid4().hex
        head = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                       for name, value in fields)
        head += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sound.wav"\r\n'
                 'Content-Type: audio/wav\r\n\r\n')
        self.head, self.tail = head.encode('utf-8'), f'\r\n--{boundary}--\r\n'.encode('ascii')
        self.handle, self.size, self.cause = handle, size, cause
        self.content_type = f'multipart/form-data; boundary={boundary}'
        self.rewind()

    def rewind(self) -> None:
        self.handle.seek(0)
        self._pending, self._left, self._done = self.head, self.size, False

    def __len__(self) -> int:
        return len(self.head) + self.size + len(self.tail)

    def read(self, size: int = -1) -> bytes:
        if self.cause():
            raise UploadStopped()
        size = 1 << 20 if size is None or size < 0 else size
        out = bytearray()
        while len(out) < size and not (self._done and not self._pending):
            if self._pending:
                take = self._pending[:size - len(out)]
                out += take
                self._pending = self._pending[len(take):]
            elif self._left:
                chunk = self.handle.read(min(size - len(out), self._left))
                if not chunk:
                    raise OSError("the sound file shrank while it was sent")
                out += chunk
                self._left -= len(chunk)
            else:
                self._pending, self._done = self.tail, True
        return bytes(out)


def _send(job_id: int, body: _Multipart):
    """One attempt: ``(report, retry)``; ``retry`` only for a 5xx or a failure before the manager had the body."""
    try:
        body.rewind()
        headers = dict(api_extras.get_auth_headers(), **{'Content-Type': body.content_type})
        response = api_extras.get_session().request('post', api_extras._url(f"jobs/{int(job_id)}/{SOUND_RESULT}/"),
                                                    data=body, headers=headers, timeout=SOUND_TIMEOUT_SECONDS)
    except UploadStopped:
        return api_extras.PilotReport('stopped', None, 'stopped by a shutdown or cancel'), False
    except requests.exceptions.ReadTimeout as exc:  # sent, but no answer: the manager may still store it
        return api_extras.PilotReport('failed', None, f"{type(exc).__name__}: not sent again"), False
    except requests.exceptions.RequestException as exc:
        return api_extras.PilotReport('failed', None, f"{type(exc).__name__}: {exc}"[:api_extras.MAX_DETAIL]), True
    except OSError as exc:
        return api_extras.PilotReport('rejected', None, f"the sound file could not be sent ({type(exc).__name__})"), False
    report = api_extras._classify(response)
    if report is not None:
        return report, False
    return api_extras.PilotReport('failed', response.status_code, api_extras._detail(response)), True


def upload_sound(job_id: int, handle, offset: dict, skipped: bool, render_s, summary: str,
                 sleep: Callable[[float], None] = time.sleep, cause: Callable[[], str] = lambda: '') \
        -> api_extras.PilotReport:
    """FR-SA-6: the run's ``.wav`` and its offset to ``sound-result/``. Never raises.

    ``cause`` (``Delivery.cause``) is checked before each attempt and at every block sent: a stop gives the outcome
    ``stopped``. A read timeout after the body went out is ``failed`` at once, never sent again (the manager may still
    be storing it; a second copy would double its disk use): the job stays ``RENDERING`` for reconciliation."""
    try:
        size = os.fstat(handle.fileno()).st_size
    except OSError as exc:
        return api_extras.PilotReport('rejected', None, f"the sound file could not be read ({type(exc).__name__})")
    if size > MAX_SOUND_BYTES:
        return api_extras.PilotReport('rejected', 413, f"the sound file is larger than {MAX_SOUND_BYTES} bytes")
    fields = [('offset', offset_text(offset)), ('latency_compensation_skipped', 'true' if skipped else 'false'),
              ('render_s', f"{min(max(float(render_s), 0.0), float(MAX_SECONDS)):.3f}"),
              ('summary', summary.encode('utf-8')[:SUMMARY_MAX_BYTES].decode('utf-8', 'ignore'))]
    body = _Multipart(fields, handle, size, cause)
    report = api_extras.PilotReport('failed')
    for attempt in range(api_extras.PILOT_ATTEMPTS):
        if cause():
            return api_extras.PilotReport('stopped', None, 'stopped by a shutdown or cancel')
        report, retry = _send(job_id, body)
        if not retry:
            return report
        if attempt + 1 < api_extras.PILOT_ATTEMPTS:
            logger.warning("sound-result/ of job %s failed (%s); retrying.", job_id, report.detail or report.status_code)
            sleep(api_extras.PILOT_BACKOFF_SECONDS[min(attempt, len(api_extras.PILOT_BACKOFF_SECONDS) - 1)])
    return report
