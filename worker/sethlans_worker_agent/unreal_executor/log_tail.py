# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bounded UE log reading (Unreal foundation FR-UE-11 "Log tail", FR-UE-12;
Sec S3).

* ``LogTail`` opens the log only **after** it appears (Python's ``open()``
  does not pass ``FILE_SHARE_DELETE``, so opening first could break UE's
  startup rename) and returns only new complete lines. Undecodable bytes
  are replaced; a line longer than ``MAX_LINE_BYTES`` is cut into pieces,
  so neither memory nor the per-line rule cost grows with a hostile line.
* ``iter_log_lines(path)`` reads a whole log through the same splitter
  (post-exit validation, Zen log verification). It raises ``OSError``
  when the file cannot be opened.

No dependency on the rest of the package, so the log rules and the runner
can both use it.
"""
import os
from pathlib import Path
from typing import Iterator, List

MB = 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_READ_BYTES = 8 * MB
_BOM = b'\xef\xbb\xbf'


class LogTail:

    def __init__(self, path):
        self._path = Path(path)
        self._handle = None
        self._partial = b''
        self.bytes_read = 0

    def _decode(self, pieces: List[bytes]) -> List[str]:
        return [piece.decode('utf-8', errors='replace').rstrip('\r') for piece in pieces]

    def read_new(self) -> List[str]:
        if self._handle is None:
            if not os.path.exists(self._path):
                return []
            self._handle = open(self._path, 'rb')
        data = self._handle.read(MAX_READ_BYTES)
        if not data:
            return []
        first_read = self.bytes_read == 0
        self.bytes_read += len(data)
        if first_read and data.startswith(_BOM):
            data = data[len(_BOM):]
        pieces = (self._partial + data).split(b'\n')
        self._partial = pieces.pop()
        while len(self._partial) > MAX_LINE_BYTES:
            pieces.append(self._partial[:MAX_LINE_BYTES])
            self._partial = self._partial[MAX_LINE_BYTES:]
        return self._decode(pieces)

    def take_partial(self) -> List[str]:
        """The unterminated last line, if any (EOF)."""
        partial, self._partial = self._partial, b''
        return self._decode([partial]) if partial else []

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()


def iter_log_lines(path) -> Iterator[str]:
    """Every line of ``path`` to EOF, through the bounded splitter."""
    with open(path, 'rb'):
        pass  # raise OSError now for a missing or unreadable file
    tail = LogTail(path)
    try:
        while True:
            before = tail.bytes_read
            yield from tail.read_new()
            if tail.bytes_read == before:
                break
        yield from tail.take_partial()
    finally:
        tail.close()
