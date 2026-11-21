# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal sequence request size classes of spec D21, for tests.

Chunk 1 added the class table with no new path; each class joined
``body_limits.BODY_CLASSES`` with its endpoint, the sound class last (chunk
5), so the live table now equals this one. The tests still install it
(``use_d21_classes``) to check that every layer applies a class from the
table alone.
"""

from __future__ import annotations

from sethlans_manager import body_limits
from sethlans_manager.body_limits import (
    FRAME_BODY_LIMIT,
    RESULT_BODY_LIMIT,
    SOUND_BODY_LIMIT,
    UPLOAD_CLASS,
    BodyClass,
)

FRAME_PATHS = ('/api/jobs/*/frames/',)
SOUND_PATHS = ('/api/jobs/*/sound-result/',)
RESULT_PATHS = (
    '/api/jobs/*/shots-result/',
    '/api/jobs/*/piece-result/',
    '/api/sequence-submissions/',
)

FRAME_CLASS = BodyClass('frame', FRAME_BODY_LIMIT, FRAME_PATHS)
SOUND_CLASS = BodyClass('sound', SOUND_BODY_LIMIT, SOUND_PATHS)
RESULT_CLASS = BodyClass('result', RESULT_BODY_LIMIT, RESULT_PATHS)

D21_CLASSES = (UPLOAD_CLASS, FRAME_CLASS, SOUND_CLASS, RESULT_CLASS)


def use_d21_classes(monkeypatch):
    """Install the D21 table as ``body_limits.BODY_CLASSES`` for one test."""
    monkeypatch.setattr(body_limits, 'BODY_CLASSES', D21_CLASSES)
    return D21_CLASSES
