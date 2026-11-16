# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Output-format settings in the generated Blender script (#243).

Which ``render.image_settings`` values Blender accepts depends on the file
format set at that moment: ``color_depth`` is ``8``/``16`` for PNG and TIFF,
``8`` for JPEG, BMP and Targa, ``16``/``32`` for OpenEXR and ``32`` for HDR.
The manager's settings can arrive in any key order (PostgreSQL and MySQL
re-sort JSON object keys), so the script applies the format group in a
fixed order, whatever order the keys arrive in:

1. ``media_type`` (Blender 5.x only, see below);
2. ``render.image_settings.file_format`` (when absent but other image
   settings are present: the format the worker passes with ``-F``, since
   ``-F`` is applied only after the script);
3. every other ``render.image_settings.*`` key, in arrival order.

The group sits where the first ``render.image_settings.*`` key arrived;
every other setting keeps its relative order. Nothing is clamped or
corrected: a wrong combination (depth 16 with JPEG) still raises.

Blender 5.x added ``image_settings.media_type`` (absent on 4.x): a
multilayer EXR needs ``MULTI_LAYER_IMAGE`` and every other still format
``IMAGE``, and a format of the other kind is rejected until the media type
is switched (measured on 5.0.1 and 5.2.2, including from a multilayer
state). So before the file format, the script switches the media type when
the property exists and its value differs. This is a named, version-specific
exception like ``cycles.feature_set`` (#241).
"""
from sethlans_worker_agent.format_utils import get_format_and_extension

IMAGE_SETTINGS_PREFIX = 'render.image_settings.'
FILE_FORMAT_KEY = 'render.image_settings.file_format'

# Still-image formats the product offers -> the media type Blender 5.x needs.
FORMAT_MEDIA_TYPES = {
    'PNG': 'IMAGE',
    'JPEG': 'IMAGE',
    'OPEN_EXR': 'IMAGE',
    'OPEN_EXR_MULTILAYER': 'MULTI_LAYER_IMAGE',
    'TIFF': 'IMAGE',
    'BMP': 'IMAGE',
    'HDR': 'IMAGE',
    'TARGA': 'IMAGE',
}


def order_settings(settings):
    """Return ``settings`` items with the format group in dependency order.

    The group (file format first, then the other image settings in arrival
    order) takes the position of the first ``render.image_settings.*`` key;
    all other items keep their relative order. When there are image settings
    but no file format, the group starts with the format the worker passes
    with ``-F`` (``format_utils.get_format_and_extension``); with no image
    settings nothing is added.
    """
    items = list(settings.items())
    group = [kv for kv in items if kv[0] == FILE_FORMAT_KEY]
    if not group and any(k.startswith(IMAGE_SETTINGS_PREFIX) for k, _ in items):
        # Image settings without a format: apply the format the worker passes
        # with -F (applied only after the script) first, so e.g. a depth is
        # checked against the output format, not the .blend file's own.
        group = [(FILE_FORMAT_KEY, get_format_and_extension(settings)[0])]
    group += [kv for kv in items if kv[0].startswith(IMAGE_SETTINGS_PREFIX) and kv[0] != FILE_FORMAT_KEY]
    ordered, placed = [], False
    for key, value in items:
        if not key.startswith(IMAGE_SETTINGS_PREFIX):
            ordered.append((key, value))
        elif not placed:
            ordered.extend(group)
            placed = True
    return ordered


def media_type_lines(file_format):
    """Script lines (in the scene loop) that switch the media type for ``file_format``.

    Empty for a value that is not an offered format: its plain assignment
    then fails or succeeds on its own.
    """
    media = FORMAT_MEDIA_TYPES.get(file_format) if isinstance(file_format, str) else None
    if media is None:
        return []
    return [
        "    if (hasattr(scene.render.image_settings, 'media_type')"
        f" and scene.render.image_settings.media_type != {ascii(media)}):",
        f"        print({ascii(f'Switching media type to {media} for file format {file_format}')})",
        f"        scene.render.image_settings.media_type = {ascii(media)}",
    ]
