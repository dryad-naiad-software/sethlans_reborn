# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Path converters for the Blender build routes (BS-MGR-12).

Django embeds a converter's ``regex`` in the route's pattern, which is
anchored at both ends, so anything but a full ``X.Y.Z`` version (one to
three digits each) or one of the six platform ids is a 404 before any view
runs.
"""

import re

from shared.blender_sources import PLATFORM_EXTENSIONS


class BlenderVersionConverter:
    regex = r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


class BlenderPlatformConverter:
    regex = '|'.join(re.escape(platform) for platform in sorted(PLATFORM_EXTENSIONS))

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value
