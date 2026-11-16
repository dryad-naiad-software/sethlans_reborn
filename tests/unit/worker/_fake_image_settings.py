# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A fake ``render.image_settings`` that enforces Blender's format rules (#243).

Modelled on measurements of official builds (4.1.1, 4.2.23, 4.5.16, 5.0.1,
5.2.2), assignment only:

- ``color_depth`` must be valid for the file format set at that moment
  (``DEPTHS``); setting a format moves an invalid depth to one the format
  accepts without an error, as Blender does. The fake picks the format's
  default depth; which depth Blender picks was not measured for every pair,
  and no test depends on it.
- The 5.x flavour has ``media_type``: ``OPEN_EXR_MULTILAYER`` needs
  ``MULTI_LAYER_IMAGE`` and every other still format ``IMAGE``; a format of
  the other kind raises Blender's enum ``TypeError``. Switching the media
  type moves the format to that kind's default.
- The 4.x flavour has no ``media_type``; any offered format can be set.

Unknown attributes raise ``AttributeError`` as on a ``bpy_struct``.
"""

DEPTHS = {
    'PNG': ('8', '16'), 'JPEG': ('8',), 'OPEN_EXR': ('16', '32'),
    'OPEN_EXR_MULTILAYER': ('16', '32'), 'TIFF': ('8', '16'), 'BMP': ('8',),
    'HDR': ('32',), 'TARGA': ('8',),
}
DEFAULT_DEPTH = {
    'PNG': '8', 'JPEG': '8', 'OPEN_EXR': '32', 'OPEN_EXR_MULTILAYER': '16',
    'TIFF': '16', 'BMP': '8', 'HDR': '32', 'TARGA': '8',
}
MEDIA_DEFAULT_FORMAT = {'IMAGE': 'PNG', 'MULTI_LAYER_IMAGE': 'OPEN_EXR_MULTILAYER'}


def _enum_error(value, allowed):
    return TypeError(f'bpy_struct: item.attr = val: enum "{value}" not found in {tuple(allowed)}')


class FakeImageSettings:
    """``ImageFormatSettings`` of a 4.x (``media_type=None``) or 5.x build."""

    def __init__(self, media_type=None, file_format='PNG', color_depth=None, quality=90):
        state = {'file_format': file_format, 'color_depth': color_depth or DEFAULT_DEPTH[file_format],
                 'quality': quality}
        if media_type is not None:
            state['media_type'] = media_type
        object.__setattr__(self, '_state', state)
        object.__setattr__(self, 'history', [])  # (attribute, value) of every accepted write

    def __getattr__(self, name):
        state = object.__getattribute__(self, '_state')
        if name in state:
            return state[name]
        raise AttributeError(f"'ImageFormatSettings' object has no attribute '{name}'")

    def _formats_allowed(self):
        if 'media_type' not in self._state:
            return tuple(DEPTHS)
        multi = self._state['media_type'] == 'MULTI_LAYER_IMAGE'
        return tuple(f for f in DEPTHS if (f == 'OPEN_EXR_MULTILAYER') == multi)

    def __setattr__(self, name, value):
        state = self._state
        if name not in state:
            raise AttributeError(f"'ImageFormatSettings' object has no attribute '{name}'")
        if name == 'file_format':
            if value not in self._formats_allowed():
                raise _enum_error(value, self._formats_allowed())
            if state['color_depth'] not in DEPTHS[value]:
                state['color_depth'] = DEFAULT_DEPTH[value]
        elif name == 'color_depth' and value not in DEPTHS[state['file_format']]:
            raise _enum_error(value, DEPTHS[state['file_format']])
        elif name == 'media_type':
            if value not in MEDIA_DEFAULT_FORMAT:
                raise _enum_error(value, tuple(MEDIA_DEFAULT_FORMAT))
            if value != state['media_type']:
                state['file_format'] = MEDIA_DEFAULT_FORMAT[value]
                state['color_depth'] = DEFAULT_DEPTH[state['file_format']]
        state[name] = value
        self.history.append((name, value))
