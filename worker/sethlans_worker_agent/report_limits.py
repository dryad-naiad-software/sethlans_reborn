# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Size bounds for render text sent to the manager as JSON (#260).

The manager caps every request body except file uploads at 128 KiB. The
final status report (``PATCH jobs/{id}/``) carries ``last_output`` and
``error_message``. ``last_output`` is the renderer's whole stdout: Cycles
prints a progress line per sample update and a line per synchronised
object, and a multi-frame job adds every frame, so big scenes reach several
MB. The manager answers such a body with 413, which is not retried, and the
job would stay RENDERING after its output was uploaded.

Sizes are measured after JSON encoding (``json.dumps`` with the default
``ensure_ascii=True``, as ``requests`` encodes ``json=``): every non-ASCII
character becomes a ``\\uXXXX`` escape (two for a character outside the
BMP) and control characters, quotes and backslashes are escaped too, so the
encoded size can be several times the character count. Escaping is per
character, so the size of a concatenation is the sum of its parts.

* ``bounded_tail`` keeps the longest end of the text that fits
  ``LAST_OUTPUT_MAX_BYTES``, starting at a line start when the kept part
  holds one, after the ``OMITTED_HEAD_MARKER`` line: the end of a render log
  shows how it finished.
* ``bounded_head`` keeps the longest start that fits
  ``ERROR_MESSAGE_MAX_BYTES``, followed by ``OMITTED_TAIL_MARKER``: the
  start of an error message names the failure.

Together they keep the status report far below the manager's limit. Text
within its bound is returned unchanged, and so is a value that is not a
string. Only what is sent is bounded; the local log keeps the full output.
"""
import json

LAST_OUTPUT_MAX_BYTES = 64 * 1024
ERROR_MESSAGE_MAX_BYTES = 16 * 1024
OMITTED_HEAD_MARKER = "[... earlier output omitted ...]\n"
OMITTED_TAIL_MARKER = " [... rest omitted ...]"


def json_size(text: str) -> int:
    """Bytes ``text`` takes as a JSON string, quotes included (the encoding is pure ASCII)."""
    return len(json.dumps(text))


def _budget(max_bytes: int, marker: str) -> int:
    """JSON size left for the kept text once ``marker`` shares its quotes."""
    return max_bytes - json_size(marker) + 2


def _line_start(text: str, start: int) -> int:
    """Move a mid-line ``start`` to the next line start, unless that leaves nothing."""
    if start == 0 or text[start - 1] == '\n':
        return start
    newline = text.find('\n', start)
    if newline == -1 or newline + 1 >= len(text):
        return start
    return newline + 1


def bounded_tail(text, max_bytes: int = LAST_OUTPUT_MAX_BYTES):
    """Return ``text``, or its marked tail, so that its JSON form is at most ``max_bytes``."""
    if not isinstance(text, str) or json_size(text) <= max_bytes:
        return text
    budget = _budget(max_bytes, OMITTED_HEAD_MARKER)
    # Every character costs at least one byte, so the tail is at most budget - 2 characters.
    lo, hi = max(0, len(text) - budget), len(text)
    while lo < hi:  # smallest start whose tail fits
        mid = (lo + hi) // 2
        if json_size(text[mid:]) <= budget:
            hi = mid
        else:
            lo = mid + 1
    return OMITTED_HEAD_MARKER + text[_line_start(text, lo):]


def bounded_head(text, max_bytes: int = ERROR_MESSAGE_MAX_BYTES):
    """Return ``text``, or its marked head, so that its JSON form is at most ``max_bytes``."""
    if not isinstance(text, str) or json_size(text) <= max_bytes:
        return text
    budget = _budget(max_bytes, OMITTED_TAIL_MARKER)
    lo, hi = 0, min(len(text), budget)
    while lo < hi:  # largest end whose head fits
        mid = (lo + hi + 1) // 2
        if json_size(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + OMITTED_TAIL_MARKER
