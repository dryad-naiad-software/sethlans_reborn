# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What an Unreal render ran with (GPU memory reader D23, FR-GC-2, FR-GC-4, FR-GC-7): the one home of the
words the worker's prep reports and the manager stores.

* ``GAME_OVERRIDES_VALUES``: whether the preset carries its own Game Overrides (``preset``), relies on
  the one Movie Render Queue adds (``implicit``), has none in effect (``none``) or could not be read.
* ``TEXTURE_STREAMING_VALUES``: the texture streaming the render ran with.

Both are sorted tuples (the manager's OpenAPI enums are built from them). ``UNKNOWN`` is in both and is
what an absent or invalid value becomes. The UE-side ``sethlans_preset_facts.py`` runs inside Unreal and
cannot import this module; it keeps the same words as literals, checked by a contract test.
"""

UNKNOWN = 'unknown'
GAME_OVERRIDES_VALUES = ('implicit', 'none', 'preset', UNKNOWN)
TEXTURE_STREAMING_VALUES = ('fully_load', 'off', 'unchanged', UNKNOWN)
