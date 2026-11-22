# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: ``shared.unreal.validation.target_sha256`` with the draft
values (Unreal per-machine memory test OQ-1, FR-MT-R-3).

- Without a draft (``None``, empty, or anything that is not an object)
  every key is unchanged: the literal
  digests below were computed from the formula before the draft argument
  existed, for the four-part, resolution, shot-set and both forms.
- A non-empty draft adds the resolution text and shot set (or ``''``) and
  then the draft's canonical JSON (sorted keys, compact).
- Key order inside the draft does not matter; equal drafts give equal
  hashes; any draft value changes the hash.
"""

import hashlib
import json

import pytest

from shared.unreal import validation as v

BUILD = '5.8.4-58902052'
MAP, SEQ, PRESET = '/Game/Maps/Dock.Dock', '/Game/Cine/Ep02.Ep02', '/Game/Presets/Farm.Farm'
BASE = (BUILD, MAP, SEQ, PRESET)
SHOT_SET = v.shot_set_sha256([(0, 120), (300, 360)])
DRAFT = {'temporal_samples': 1, 'spatial_samples': 1, 'screen_percentage': 50, 'motion_blur': False}

# Computed from the pre-change ``target_sha256`` (no draft argument).
PINNED = {
    'four': '8e140454a5e6cf3cc580210c894c1fd37c2b22d904c8c10b1351528fdec47910',
    'res': 'ea5df4944f36e15f91842e279a210a0e706782815c390889c68ce9ef9476c3b9',
    'shot': 'df579d8f9421963a0db69e2fd5b5b42de970e44c3ea3e4328b20ca9f4c5c9d92',
    'both': '253c40303a00a20f7ac8f8c6dd9987818eca1b4e2e6cc1c4148acbffc0da7292',
}
FORMS = {'four': (None, None), 'res': ([1920, 1080], None), 'shot': (None, SHOT_SET), 'both': ([1920, 1080], SHOT_SET)}


def _expected(parts):
    text = json.dumps(parts, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('ascii')).hexdigest()


class TestNoDraftUnchanged:

    @pytest.mark.parametrize('form', sorted(FORMS))
    def test_default_draft_keeps_the_pinned_hash(self, form):
        resolution, shot_set = FORMS[form]
        assert v.target_sha256(*BASE, resolution, shot_set) == PINNED[form]

    @pytest.mark.parametrize('draft', [None, {}], ids=['none', 'empty-dict'])
    @pytest.mark.parametrize('form', sorted(FORMS))
    def test_explicit_no_draft_keeps_the_pinned_hash(self, form, draft):
        resolution, shot_set = FORMS[form]
        assert v.target_sha256(*BASE, resolution, shot_set, draft) == PINNED[form]

    @pytest.mark.parametrize('draft', [[], [1], ['temporal_samples', 1], 'draft', '', 0, 3, True, False, ()],
                             ids=['empty-list', 'list', 'pairs-list', 'str', 'empty-str', 'zero', 'int', 'true',
                                  'false', 'tuple'])
    @pytest.mark.parametrize('form', sorted(FORMS))
    def test_a_non_dict_draft_is_no_draft(self, form, draft):
        """One rule for ``Job`` and ``Target``: only a non-empty object is a draft."""
        resolution, shot_set = FORMS[form]
        assert v.target_sha256(*BASE, resolution, shot_set, draft) == PINNED[form]

    def test_draft_keyword(self):
        assert v.target_sha256(*BASE, draft=None) == PINNED['four']
        assert v.target_sha256(*BASE, draft={}) == PINNED['four']


class TestDraftChangesTheHash:

    @pytest.mark.parametrize('form', sorted(FORMS))
    def test_draft_gives_a_new_hash_in_every_form(self, form):
        resolution, shot_set = FORMS[form]
        assert v.target_sha256(*BASE, resolution, shot_set, DRAFT) != PINNED[form]

    def test_draft_alone_adds_empty_size_and_shot_set_then_the_json(self):
        draft_json = '{"motion_blur":false,"screen_percentage":50,"spatial_samples":1,"temporal_samples":1}'
        assert v.target_sha256(*BASE, draft=DRAFT) == _expected([*BASE, '', '', draft_json])

    def test_draft_with_size_and_shot_set(self):
        draft_json = '{"screen_percentage":50}'
        digest = v.target_sha256(*BASE, [1920, 1080], SHOT_SET, {'screen_percentage': 50})
        assert digest == _expected([*BASE, '1920x1080', SHOT_SET, draft_json])

    def test_draft_without_size_differs_from_size_only_form(self):
        assert v.target_sha256(*BASE, draft=DRAFT) != v.target_sha256(*BASE, [1920, 1080])
        assert v.target_sha256(*BASE, draft=DRAFT) != v.target_sha256(*BASE, None, SHOT_SET)

    @pytest.mark.parametrize('key, value', [('temporal_samples', 2), ('spatial_samples', 4),
                                            ('screen_percentage', 51), ('motion_blur', True)])
    def test_each_draft_value_matters(self, key, value):
        assert v.target_sha256(*BASE, draft=dict(DRAFT, **{key: value})) != v.target_sha256(*BASE, draft=DRAFT)

    def test_extra_draft_key_matters(self):
        more = dict(DRAFT, ray_traced_shadows=False)
        assert v.target_sha256(*BASE, draft=more) != v.target_sha256(*BASE, draft=DRAFT)

    def test_bool_and_int_draft_values_differ(self):
        assert v.target_sha256(*BASE, draft={'motion_blur': False}) != v.target_sha256(*BASE, draft={'motion_blur': 0})


class TestDraftCanonical:

    def test_key_order_does_not_matter(self):
        reordered = dict(reversed(list(DRAFT.items())))
        assert list(reordered) != list(DRAFT)
        assert v.target_sha256(*BASE, draft=reordered) == v.target_sha256(*BASE, draft=DRAFT)

    def test_equal_drafts_equal_hashes(self):
        assert v.target_sha256(*BASE, [1920, 1080], SHOT_SET, dict(DRAFT)) == (
            v.target_sha256(*BASE, [1920, 1080], SHOT_SET, dict(DRAFT)))

    def test_non_ascii_value_is_escaped(self):
        digest = v.target_sha256(*BASE, draft={'n': 'é'})
        assert digest == _expected([*BASE, '', '', '{"n":"\\u00e9"}'])

    @pytest.mark.parametrize('draft', [{'a': {1, 2}}, {1: 'x', 'b': 2}, {'a': float('nan')}],
                             ids=['set-value', 'mixed-keys', 'nan'])
    def test_never_raises(self, draft):
        digest = v.target_sha256(*BASE, draft=draft)
        assert len(digest) == 64 and digest == digest.lower()

    def test_draft_does_not_change_the_dict(self):
        draft = dict(DRAFT)
        v.target_sha256(*BASE, draft=draft)
        assert draft == DRAFT and list(draft) == list(DRAFT)
