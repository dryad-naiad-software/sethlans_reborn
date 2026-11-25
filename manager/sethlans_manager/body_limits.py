# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Request body limits by path class (issue #260).

Every request path falls into one class of :data:`BODY_CLASSES`, or into
none, which is the small class:

* **Upload paths** take a file in the body (``.blend`` assets, worker
  render output, the Django admin asset form). They keep the large
  limit, ``UPLOAD_BODY_LIMIT``.
* **Every other path** takes JSON or a small form. Its limit is
  ``SMALL_BODY_LIMIT``.

The same table drives every layer that serves the manager, so the
layers cannot disagree on a path's class:

* the Caddyfile (``caddy_body_classes`` for the template, and the static
  Docker Caddyfile): one named matcher and one ``request_body`` per
  class, plus a small matcher that excludes every class's paths;
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

The Unreal sequence classes (spec D21) join the table with their
endpoints: ``FRAME_BODY_LIMIT`` is one frame of up to 512 MiB (a large
EXR), ``SOUND_BODY_LIMIT`` one ``.wav`` of up to 4 GiB (a 32-bit RIFF
size), each plus the same 2 MiB headroom; ``RESULT_BODY_LIMIT`` (1 MiB)
is a worker's JSON result or a submission, as a 2,000-shot result
exceeds 128 KiB.

Patterns follow Caddy's ``path`` matcher: a trailing ``*`` is a prefix
match, a ``*`` inside the pattern matches one path segment. Matching
here is case-sensitive, like Django's URL resolver; Caddy's is not,
which only lets a case variant through Caddy to be refused by Waitress
or answered 404 by Django. The classes' paths must not overlap: Caddy
would apply every matching limit, this module only the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIB = 1024 * 1024
GIB = 1024 * MIB

# Asset serializer and output upload both cap the file at 10 GiB.
UPLOAD_FILE_CAP = 10 * GIB
UPLOAD_HEADROOM = 2 * MIB
UPLOAD_BODY_LIMIT = UPLOAD_FILE_CAP + UPLOAD_HEADROOM
SMALL_BODY_LIMIT = 128 * 1024

# Unreal sequence classes (spec D21); the file caps are the views' own.
FRAME_FILE_CAP = 512 * MIB
FRAME_BODY_LIMIT = FRAME_FILE_CAP + UPLOAD_HEADROOM
SOUND_FILE_CAP = 4 * GIB
SOUND_BODY_LIMIT = SOUND_FILE_CAP + UPLOAD_HEADROOM
RESULT_BODY_LIMIT = 1 * MIB

# Only the Asset admin form is an upload path. The file fields of the
# Job and Animation admin forms get the small limit, so replacing a file
# there is refused with 413 (not a supported workflow).
UPLOAD_PATHS = (
    '/api/assets/*',
    '/api/jobs/*/upload_output/',
    '/admin/workers/asset/add/',
    '/admin/workers/asset/*/change/',
)

# Lower-case letters only: the name becomes the Caddy matcher ``@<name>_body``.
_CLASS_NAME_RE = re.compile(r'[a-z]+')


def _pattern_regex(pattern: str) -> str:
    """Translate one Caddy ``path`` pattern into a regular expression."""
    prefix = pattern.endswith('*')
    parts = (pattern[:-1] if prefix else pattern).split('*')
    body = '[^/]*'.join(re.escape(part) for part in parts)
    return body + ('.*' if prefix else '')


@dataclass(frozen=True)
class BodyClass:
    """One request size class: its name, its limit in bytes and its path patterns."""

    name: str
    limit: int
    paths: tuple
    _regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.name, str) or not _CLASS_NAME_RE.fullmatch(self.name) or self.name == 'small':
            raise ValueError(f"Invalid body class name {self.name!r}.")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError(f"Body class {self.name!r} needs a positive integer limit.")
        if not self.paths or not all(isinstance(p, str) and p.startswith('/') for p in self.paths):
            raise ValueError(f"Body class {self.name!r} needs path patterns starting with '/'.")
        regex = re.compile('|'.join(f'(?:{_pattern_regex(p)})' for p in self.paths), re.DOTALL)
        object.__setattr__(self, '_regex', regex)

    def matches(self, path: str) -> bool:
        """Return True when *path* (the decoded request path) is in this class."""
        return bool(self._regex.fullmatch(path or ''))


def check_disjoint(classes) -> None:
    """Raise ``ValueError`` when two classes share a name or a pattern of one
    (each ``*`` as ``1``) matches another class."""
    if len({c.name for c in classes}) != len(classes):
        raise ValueError("Body class names must be unique.")
    for body_class in classes:
        for pattern in body_class.paths:
            probe = pattern.replace('*', '1')
            others = [c.name for c in classes if c is not body_class and c.matches(probe)]
            if others:
                raise ValueError(f"Body class {body_class.name!r} overlaps {others} on {pattern!r}.")


UPLOAD_CLASS = BodyClass('upload', UPLOAD_BODY_LIMIT, UPLOAD_PATHS)

# D21's classes.
FRAME_PATHS = (
    '/api/jobs/*/frames/',
)
FRAME_CLASS = BodyClass('frame', FRAME_BODY_LIMIT, FRAME_PATHS)
SOUND_PATHS = ('/api/jobs/*/sound-result/',)
SOUND_CLASS = BodyClass('sound', SOUND_BODY_LIMIT, SOUND_PATHS)
RESULT_PATHS = (
    '/api/jobs/*/shots-result/',
    '/api/jobs/*/piece-result/',
    '/api/sequence-submissions/',
)
RESULT_CLASS = BodyClass('result', RESULT_BODY_LIMIT, RESULT_PATHS)

# Every class but the small one. Read at call time by every layer.
BODY_CLASSES = (UPLOAD_CLASS, FRAME_CLASS, SOUND_CLASS, RESULT_CLASS)
check_disjoint(BODY_CLASSES)


# Setup mode (``run_manager --setup``) serves only ``/api/setup/*`` and
# its page, so its one server takes no body above this limit, and Django
# there checks the same number (``settings_setup``). Not in BODY_CLASSES:
# the running manager has no setup paths (they answer 404), and the
# classes there all sit above the small limit.
SETUP_BODY_LIMIT = 64 * 1024
SETUP_PATHS = ('/api/setup/*',)
SETUP_CLASS = BodyClass('setup', SETUP_BODY_LIMIT, SETUP_PATHS)


def body_class_for_path(path: str) -> BodyClass | None:
    """Return the first class of :data:`BODY_CLASSES` whose patterns match *path*."""
    for body_class in BODY_CLASSES:
        if body_class.matches(path):
            return body_class
    return None


def is_upload_path(path: str) -> bool:
    """Return True when *path* (the decoded request path) is an upload path.

    Kept as the issue #260 classifier the tests check the upload paths with.
    """
    return UPLOAD_CLASS.matches(path)


def body_limit_for_path(path: str, upload_limit: int = UPLOAD_BODY_LIMIT) -> int:
    """Return the largest body size in bytes accepted on *path*.

    ``upload_limit`` is the listener's ceiling: upload paths get it, and
    every other class's limit is capped by it.
    """
    body_class = body_class_for_path(path)
    if body_class is UPLOAD_CLASS:
        return upload_limit
    limit = SMALL_BODY_LIMIT if body_class is None else body_class.limit
    return min(limit, upload_limit)
