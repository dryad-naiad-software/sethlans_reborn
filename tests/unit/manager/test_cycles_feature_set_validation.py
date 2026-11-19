# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the Cycles feature set / Blender version rule (#244).

Blender 5.0 removed ``cycles.feature_set``. The experimental feature set is
refused for a job whose effective Blender version is 5.0 or later; the
supported feature set is valid on every version.
"""

from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from workers.constants import CyclesFeatureSet
from workers.serializers import validation_utils
from workers.serializers.validation_utils import (
    CYCLES_FEATURE_SET_REMOVED_IN,
    blender_has_cycles_feature_set,
    blender_version_tuple,
    validate_cycles_feature_set,
)

EXP = CyclesFeatureSet.EXPERIMENTAL
SUP = CyclesFeatureSet.SUPPORTED


def _version(series):
    """A stand-in for ``SupportedBlenderVersion`` (only ``series`` is read)."""
    return SimpleNamespace(series=series)


def _asset(series):
    """An asset whose project renders with Blender ``series`` (None: no version)."""
    version = _version(series) if series is not None else None
    return SimpleNamespace(project=SimpleNamespace(blender_version=version))


def _instance(feature_set=SUP, override=None, project_series='4.5'):
    return SimpleNamespace(
        cycles_feature_set=feature_set,
        blender_version=_version(override) if override else None,
        asset=_asset(project_series),
    )


def _refusal(attrs, instance=None):
    with pytest.raises(ValidationError) as exc_info:
        validate_cycles_feature_set(attrs, instance)
    detail = exc_info.value.detail
    assert list(detail) == ['cycles_feature_set']
    assert len(detail['cycles_feature_set']) == 1
    return str(detail['cycles_feature_set'][0])


class TestConstant:

    def test_removed_in_is_5_0(self):
        assert CYCLES_FEATURE_SET_REMOVED_IN == (5, 0)


class TestBlenderVersionTuple:

    @pytest.mark.parametrize('text,expected', [
        ('5.0', (5, 0)),
        ('5.0.1', (5, 0)),
        ('5.2.2', (5, 2)),
        ('4.5.16', (4, 5)),
        (' 4.2 ', (4, 2)),
        ('10.0', (10, 0)),
        ('4.10', (4, 10)),
    ])
    def test_parses_version_strings(self, text, expected):
        assert blender_version_tuple(text) == expected

    @pytest.mark.parametrize('value', [
        None, '', '   ', 'abc', '5.x', '5.0.x', '5.0.1.2', 'v5.0', '5.0-beta', '.5', 5, 5.0, (5, 0),
    ])
    def test_unparseable_is_none(self, value):
        assert blender_version_tuple(value) is None

    @pytest.mark.parametrize('bare_major', ['5', '6', '10'])
    def test_bare_major_is_none(self, bare_major):
        # A version without a minor part is not parsed at all, so it counts
        # as unknown (and therefore as still having the feature set).
        assert blender_version_tuple(bare_major) is None

    def test_reads_series_attribute(self):
        assert blender_version_tuple(_version('5.2')) == (5, 2)

    def test_object_series_is_parsed_like_text(self):
        assert blender_version_tuple(_version(' 4.5.16 ')) == (4, 5)

    @pytest.mark.parametrize('obj', [
        SimpleNamespace(series=None), SimpleNamespace(series=5.0), SimpleNamespace(), object(),
    ])
    def test_object_without_text_series_is_none(self, obj):
        assert blender_version_tuple(obj) is None

    def test_returns_ints(self):
        major, minor = blender_version_tuple('05.02')
        assert (major, minor) == (5, 2)
        assert type(major) is int and type(minor) is int


class TestBlenderHasCyclesFeatureSet:

    @pytest.mark.parametrize('version', ['4.1', '4.2', '4.5', '4.5.16', '3.6', '4.99'])
    def test_true_before_5_0(self, version):
        assert blender_has_cycles_feature_set(version) is True

    @pytest.mark.parametrize('version', ['5.0', '5.0.1', '5.2', '6.0', '10.0'])
    def test_false_from_5_0(self, version):
        assert blender_has_cycles_feature_set(version) is False

    @pytest.mark.parametrize('series,expected', [('4.5', True), ('5.0', False), ('5.2', False)])
    def test_reads_version_objects(self, series, expected):
        assert blender_has_cycles_feature_set(_version(series)) is expected

    @pytest.mark.parametrize('version', [None, '', 'abc', '5.x', _version(None)])
    def test_unknown_counts_as_having_it(self, version):
        assert blender_has_cycles_feature_set(version) is True

    @pytest.mark.parametrize('bare_major', ['5', '6'])
    def test_bare_major_counts_as_having_it(self, bare_major):
        # Documented current behaviour: see TestBlenderVersionTuple.
        assert blender_has_cycles_feature_set(bare_major) is True

    def test_driven_by_constant(self, monkeypatch):
        monkeypatch.setattr(validation_utils, 'CYCLES_FEATURE_SET_REMOVED_IN', (4, 5))
        assert blender_has_cycles_feature_set('4.2') is True
        assert blender_has_cycles_feature_set('4.5') is False
        assert blender_has_cycles_feature_set('5.0') is False

    def test_later_cutoff_keeps_5_x(self, monkeypatch):
        monkeypatch.setattr(validation_utils, 'CYCLES_FEATURE_SET_REMOVED_IN', (6, 0))
        assert blender_has_cycles_feature_set('5.2') is True
        assert blender_has_cycles_feature_set('6.0') is False


class TestCreate:

    def test_experimental_with_5x_override_refused(self):
        attrs = {'cycles_feature_set': EXP, 'blender_version': _version('5.2'), 'asset': _asset('4.5')}
        message = _refusal(attrs)
        assert message == (
            "The 'EXPERIMENTAL' Cycles feature set is not available in Blender 5.0 "
            "and later (this job uses Blender 5.2). Use 'SUPPORTED'."
        )

    def test_experimental_on_5x_project_without_override_refused(self):
        message = _refusal({'cycles_feature_set': EXP, 'asset': _asset('5.0')})
        assert 'this job uses Blender 5.0' in message

    def test_explicit_null_override_uses_project_version(self):
        message = _refusal({'cycles_feature_set': EXP, 'blender_version': None, 'asset': _asset('5.2')})
        assert 'this job uses Blender 5.2' in message

    def test_experimental_with_4x_override_on_5x_project_passes(self):
        attrs = {'cycles_feature_set': EXP, 'blender_version': _version('4.5'), 'asset': _asset('5.2')}
        assert validate_cycles_feature_set(attrs) is None

    def test_5x_override_on_4x_project_refused(self):
        attrs = {'cycles_feature_set': EXP, 'blender_version': _version('5.0'), 'asset': _asset('4.2')}
        assert 'this job uses Blender 5.0' in _refusal(attrs)

    @pytest.mark.parametrize('series', ['4.1', '4.2', '4.5'])
    def test_experimental_on_4x_passes(self, series):
        assert validate_cycles_feature_set({'cycles_feature_set': EXP, 'asset': _asset(series)}) is None

    @pytest.mark.parametrize('series', ['4.2', '5.0', '5.2', '6.0'])
    def test_supported_passes_everywhere(self, series):
        assert validate_cycles_feature_set({'cycles_feature_set': SUP, 'asset': _asset(series)}) is None

    def test_plain_string_value_is_compared(self):
        _refusal({'cycles_feature_set': 'EXPERIMENTAL', 'asset': _asset('5.0')})

    def test_key_absent_passes_on_5x(self):
        assert validate_cycles_feature_set({'asset': _asset('5.2')}) is None

    def test_no_asset_and_no_version_passes(self):
        assert validate_cycles_feature_set({'cycles_feature_set': EXP}) is None

    def test_project_without_version_passes(self):
        assert validate_cycles_feature_set({'cycles_feature_set': EXP, 'asset': _asset(None)}) is None

    def test_unparseable_version_passes(self):
        attrs = {'cycles_feature_set': EXP, 'blender_version': _version('junk'), 'asset': _asset('5.0')}
        assert validate_cycles_feature_set(attrs) is None

    def test_does_not_modify_attrs(self):
        attrs = {'cycles_feature_set': EXP, 'asset': _asset('4.5')}
        before = dict(attrs)
        validate_cycles_feature_set(attrs)
        assert attrs == before

    def test_message_follows_constant(self, monkeypatch):
        monkeypatch.setattr(validation_utils, 'CYCLES_FEATURE_SET_REMOVED_IN', (4, 5))
        message = _refusal({'cycles_feature_set': EXP, 'asset': _asset('4.5')})
        assert 'available in Blender 4.5 and later (this job uses Blender 4.5)' in message


class TestUpdate:

    def test_unrelated_fields_never_refused(self):
        instance = _instance(feature_set=EXP, project_series='5.2')  # stored pair invalid
        assert validate_cycles_feature_set({'status': 'RENDERING', 'progress': 'x'}, instance) is None

    def test_empty_attrs_never_refused(self):
        assert validate_cycles_feature_set({}, _instance(feature_set=EXP, override='5.0')) is None

    def test_version_changed_to_5x_on_experimental_refused(self):
        instance = _instance(feature_set=EXP, project_series='4.5')
        message = _refusal({'blender_version': _version('5.0')}, instance)
        assert 'this job uses Blender 5.0' in message

    def test_version_changed_to_4x_on_experimental_passes(self):
        instance = _instance(feature_set=EXP, override='4.2', project_series='5.2')
        assert validate_cycles_feature_set({'blender_version': _version('4.5')}, instance) is None

    def test_version_set_to_null_applies_project_version(self):
        instance = _instance(feature_set=EXP, override='4.5', project_series='5.2')
        assert 'this job uses Blender 5.2' in _refusal({'blender_version': None}, instance)

    def test_version_set_to_null_on_4x_project_passes(self):
        instance = _instance(feature_set=EXP, override='4.2', project_series='4.5')
        assert validate_cycles_feature_set({'blender_version': None}, instance) is None

    def test_asset_moved_to_5x_project_refused(self):
        instance = _instance(feature_set=EXP, project_series='4.5')
        assert 'this job uses Blender 5.0' in _refusal({'asset': _asset('5.0')}, instance)

    def test_asset_moved_to_5x_project_with_4x_override_passes(self):
        instance = _instance(feature_set=EXP, override='4.5', project_series='4.5')
        assert validate_cycles_feature_set({'asset': _asset('5.2')}, instance) is None

    def test_experimental_set_on_5x_instance_refused(self):
        instance = _instance(feature_set=SUP, project_series='5.2')
        assert 'this job uses Blender 5.2' in _refusal({'cycles_feature_set': EXP}, instance)

    def test_experimental_set_on_5x_override_instance_refused(self):
        instance = _instance(feature_set=SUP, override='5.0', project_series='4.5')
        assert 'this job uses Blender 5.0' in _refusal({'cycles_feature_set': EXP}, instance)

    def test_5x_together_with_supported_passes(self):
        instance = _instance(feature_set=EXP, project_series='4.5')
        attrs = {'blender_version': _version('5.2'), 'cycles_feature_set': SUP}
        assert validate_cycles_feature_set(attrs, instance) is None

    def test_supported_sent_on_invalid_stored_pair_passes(self):
        instance = _instance(feature_set=EXP, project_series='5.2')
        assert validate_cycles_feature_set({'cycles_feature_set': SUP}, instance) is None

    def test_experimental_resent_on_invalid_stored_pair_refused(self):
        instance = _instance(feature_set=EXP, project_series='5.2')
        _refusal({'cycles_feature_set': EXP}, instance)

    def test_experimental_set_on_4x_instance_passes(self):
        instance = _instance(feature_set=SUP, project_series='4.5')
        assert validate_cycles_feature_set({'cycles_feature_set': EXP}, instance) is None
