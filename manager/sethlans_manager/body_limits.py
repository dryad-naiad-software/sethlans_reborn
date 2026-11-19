# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Request body limits by path class (issue #260).

Every request path falls into one of two classes:

* **Upload paths** take a file in the body (``.blend`` assets, worker
  render output, the Django admin asset form). They keep the large
  limit, ``UPLOAD_BODY_LIMIT``.
* **Every other path** takes JSON or a small form. Its limit is
  ``SMALL_BODY_LIMIT``.

The same path list drives every layer that serves the manager, so the
layers cannot disagree on which path is an upload:

* the Caddyfile (``caddy_template`` and the static Docker Caddyfile):
  ``request_body`` per route matcher, the front of the stack;
* the Waitress listeners (``waitress_body_limits``): 413 from the
  request headers when ``Content-Length`` is too large (with
  ``Expect: 100-continue``, after reading up to the limit), and a bound
  on chunked bodies, before the Django app runs;
* Django's ``DATA_UPLOAD_MAX_MEMORY_SIZE``, the last line. Django
  checks it only for ``request.body`` and form fields (multipart file
  parts excluded). It does **not** cap DRF JSON: DRF's ``JSONParser``
  reads the request stream, not ``request.body``. JSON bodies are
  therefore capped by Caddy and Waitress only.

``SMALL_BODY_LIMIT`` is sized for the largest JSON the manager accepts.
A real heartbeat is about 1.4 KB (about 2.7 KB with the planned
``cards`` report). A heartbeat with every list and string at the
manager's validation caps, ``cards`` included, is about 37 KB, and the
``available_tools`` validator alone tolerates 64 KiB
(``heartbeat_validators.MAX_AVAILABLE_TOOLS_BYTES``). The pilot result
carries a summary of up to 32 KiB of UTF-8 (more once JSON-escaped)
plus 256 shots. The job status PATCH carries ``last_output``; the
worker caps Blender's log tail to fit. 128 KiB covers all of them with
margin.

``UPLOAD_BODY_LIMIT`` is the 10 GiB file cap (``MAX_BLEND_FILE_SIZE``,
the output upload's ``MAX_UPLOAD_SIZE``) plus 2 MiB of headroom for
the output upload's thumbnail (up to 1 MiB) and the multipart
envelope, so a file the serializer accepts is not refused here.

Patterns follow Caddy's ``path`` matcher: a trailing ``*`` is a prefix
match, a ``*`` inside the pattern matches one path segment. Matching
here is case-sensitive, like Django's URL resolver; Caddy's is not,
which only lets a case variant through Caddy to be refused by Waitress
or answered 404 by Django.
"""

from __future__ import annotations

import re

# Asset serializer and output upload both cap the file at 10 GiB.
UPLOAD_FILE_CAP = 10 * 1024 * 1024 * 1024
UPLOAD_HEADROOM = 2 * 1024 * 1024
UPLOAD_BODY_LIMIT = UPLOAD_FILE_CAP + UPLOAD_HEADROOM
SMALL_BODY_LIMIT = 128 * 1024

# Only the Asset admin form is an upload path. The file fields of the
# Job and Animation admin forms get the small limit, so replacing a file
# there is refused with 413 (not a supported workflow).
UPLOAD_PATHS = (
    '/api/assets/*',
    '/api/jobs/*/upload_output/',
    '/admin/workers/asset/add/',
    '/admin/workers/asset/*/change/',
)


def _pattern_regex(pattern: str) -> str:
    """Translate one Caddy ``path`` pattern into a regular expression."""
    prefix = pattern.endswith('*')
    parts = (pattern[:-1] if prefix else pattern).split('*')
    body = '[^/]*'.join(re.escape(part) for part in parts)
    return body + ('.*' if prefix else '')


_UPLOAD_RE = re.compile(
    '|'.join(f'(?:{_pattern_regex(p)})' for p in UPLOAD_PATHS), re.DOTALL,
)


def is_upload_path(path: str) -> bool:
    """Return True when *path* (the decoded request path) is an upload path."""
    return bool(_UPLOAD_RE.fullmatch(path or ''))


def body_limit_for_path(path: str, upload_limit: int = UPLOAD_BODY_LIMIT) -> int:
    """Return the largest body size in bytes accepted on *path*."""
    if is_upload_path(path):
        return upload_limit
    return min(SMALL_BODY_LIMIT, upload_limit)
