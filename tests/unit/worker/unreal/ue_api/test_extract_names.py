# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``ast`` extractor behind the verified name list (``tools/ue_api/extract_names.py``, issue #283): it follows
the ways the UE scripts spell a name (helpers that pass a property name on, prefixes, f-strings, constants,
slices, loop targets, aliases of ``unreal.X``) and reports what it cannot follow, on small made-up scripts.
"""
import textwrap

from tools.ue_api import extract_names


def names_of(tmp_path, source, other=None):
    (tmp_path / 'script.py').write_text('import unreal\n' + textwrap.dedent(source), encoding='utf-8')
    if other is not None:
        (tmp_path / 'plain.py').write_text(textwrap.dedent(other), encoding='utf-8')
    found, scripts = extract_names.extract(tmp_path)
    return found, scripts


def test_helpers_that_pass_a_property_name_on_are_followed(tmp_path):
    found, _ = names_of(tmp_path, '''
        PAIRS = (("handle_frame_count", 0), ("frame_number_offset", 0), ("output_frame_step", 1))
        GRAPH = PAIRS[:2]
        PAD = "zero_pad_frame_numbers"

        def override(node, name, value):
            node.set_editor_property("override_" + name, True)
            node.set_editor_property(name, value)

        def lower(obj, name):
            return obj.get_editor_property(name)

        def run(node, name):
            for name, value in GRAPH:  # inside its loop the target wins over the parameter of that name
                override(node, name, value)
            lower(node, PAD)
            for edge in ("start", "end"):
                node.set_editor_property(f"custom_playback_range_{edge}", 1)
    ''')
    assert found['properties'] == sorted({'override_handle_frame_count', 'handle_frame_count',
                                          'override_frame_number_offset', 'frame_number_offset',
                                          'zero_pad_frame_numbers', 'custom_playback_range_start',
                                          'custom_playback_range_end'})
    assert found['dynamic'] == []


def test_classes_statics_aliases_and_members(tmp_path):
    found, _ = names_of(tmp_path, '''
        CALLS = ("named_resolution_from_size", "is_named_resolution_valid")

        def run(seq, mode):
            library = getattr(unreal, "MovieGraphLibrary", None)
            ok = all(hasattr(library, name) for name in CALLS)
            methods = unreal.MoviePipelineTextureStreamingMethod
            for name in ("DISABLED", "NONE"):
                getattr(methods, name)
            unreal.SystemLibrary.quit_editor()
            return [t.get_sections() for t in seq.get_tracks()], ok, "x".join([]), seq.parameters.start_frame_offset
    ''')
    assert found['unreal'] == ['MovieGraphLibrary', 'MoviePipelineTextureStreamingMethod', 'SystemLibrary']
    assert found['static'] == ['MovieGraphLibrary.is_named_resolution_valid', 'MovieGraphLibrary.named_resolution_from_size',
                               'MoviePipelineTextureStreamingMethod.DISABLED', 'MoviePipelineTextureStreamingMethod.NONE',
                               'SystemLibrary.quit_editor']
    assert found['members'] == ['get_sections', 'get_tracks', 'parameters', 'start_frame_offset']  # not str.join


def test_what_cannot_be_followed_is_reported(tmp_path):
    found, _ = names_of(tmp_path, '''
        def run(node, names, cls_names):
            for name in names:
                node.get_editor_property(name.upper())
            return [getattr(unreal, c) for c in cls_names]
    ''')
    assert found['dynamic'] == ['script.py:5', 'script.py:6']


def test_only_scripts_that_import_unreal_are_read_and_own_names_are_skipped(tmp_path):
    found, scripts = names_of(tmp_path, '''
        class EditsError(RuntimeError):
            pass

        def helper(edits):
            return edits.EditsError, edits.helper
    ''', other='def f(x):\n    return x.get_editor_property("never")\n')
    assert scripts == ['script.py'] and found['members'] == [] and found['properties'] == []
