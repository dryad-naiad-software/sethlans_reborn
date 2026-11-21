# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the two required "what the render ran with" fields of the
pilot-result body (gpu-memory-reader FR-GC-7, FR-GC-8, FR-GT-12, AC-29).
"""

import pytest
from django.conf import settings
from django.utils.module_loading import import_string

from workers import constants
from workers.models import UnrealMeasurement
from workers.serializers.unreal_measurement import LatestMeasurementSerializer, PilotResultRequestSerializer

FIELDS = ('game_overrides', 'texture_streaming')
SHA = 'ab' * 32


def _body(**overrides):
    body = {
        'peak_mb': 8102, 'peak_source': 'csv', 'card_delta_peak_mb': 7930,
        'baseline_used_mb': 945, 'baseline_drift_mb': 12, 'peak_phase': 'render',
        'max_sample_gap_s': 1.2, 'prep_s': 24.1, 'warmup_s': 800.2, 'warmup_ran': True,
        'render_s': 1380.5, 'frames': 23, 'seconds_per_frame': 60.0, 'per_shot': [],
        'gpu': {'index': 0, 'name': 'Example Card', 'total_mb': 32607, 'card_key': 'pci:0000:01:00.0'},
        'engine_build': '5.8.4-58902052', 'csv_local_used_peak_mb': 8102,
        'overrides_sha256': SHA, 'summary': '', 'game_overrides': 'preset', 'texture_streaming': 'off',
        'ram': None, 'ram_process_commit_peak_mb': None, 'shared_machine': False,
    }
    body.update(overrides)
    return body


def _errors(body):
    serializer = PilotResultRequestSerializer(data=body)
    assert not serializer.is_valid()
    return serializer.errors


class TestValues:

    def test_the_value_lists_are_the_spec_s_sorted_tuples(self):
        assert constants.UNREAL_GAME_OVERRIDES == ('implicit', 'none', 'preset', 'unknown')
        assert constants.UNREAL_TEXTURE_STREAMING == ('fully_load', 'off', 'unchanged', 'unknown')
        for values in (constants.UNREAL_GAME_OVERRIDES, constants.UNREAL_TEXTURE_STREAMING):
            assert values == tuple(sorted(values))
            assert all(len(value) <= 16 for value in values)

    def test_the_model_fields_take_the_same_values_and_default_to_blank(self):
        for name, values in zip(FIELDS, (constants.UNREAL_GAME_OVERRIDES, constants.UNREAL_TEXTURE_STREAMING)):
            field = UnrealMeasurement._meta.get_field(name)
            assert (field.max_length, field.default, field.blank, field.null) == (16, '', True, False)
            assert [value for value, _label in field.choices] == list(values)

    def test_the_openapi_enums_are_pinned_to_the_tuples(self):
        overrides = settings.SPECTACULAR_SETTINGS['ENUM_NAME_OVERRIDES']
        assert import_string(overrides['UnrealGameOverridesEnum']) is constants.UNREAL_GAME_OVERRIDES
        assert import_string(overrides['UnrealTextureStreamingEnum']) is constants.UNREAL_TEXTURE_STREAMING


class TestPilotResultRequest:

    @pytest.mark.parametrize('value', constants.UNREAL_GAME_OVERRIDES)
    def test_every_game_overrides_value_is_accepted(self, value):
        serializer = PilotResultRequestSerializer(data=_body(game_overrides=value))
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data['game_overrides'] == value

    @pytest.mark.parametrize('value', constants.UNREAL_TEXTURE_STREAMING)
    def test_every_texture_streaming_value_is_accepted(self, value):
        serializer = PilotResultRequestSerializer(data=_body(texture_streaming=value))
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data['texture_streaming'] == value

    @pytest.mark.parametrize('field', FIELDS)
    @pytest.mark.parametrize('value', [
        'other', '', None, 1, True, ['preset'], {'v': 'off'}, 'PRESET', 'off ', 'Fully_Load',
    ])
    def test_a_bad_value_is_refused_on_its_field(self, field, value):
        errors = _errors(_body(**{field: value}))
        assert set(errors) == {field}

    @pytest.mark.parametrize('field', FIELDS)
    def test_a_missing_field_is_refused(self, field):
        body = _body()
        del body[field]
        errors = _errors(body)
        assert set(errors) == {field}
        assert errors[field][0].code == 'required'

    def test_the_values_of_one_field_are_not_accepted_in_the_other(self):
        assert set(_errors(_body(game_overrides='off'))) == {'game_overrides'}
        assert set(_errors(_body(texture_streaming='preset'))) == {'texture_streaming'}


class TestLatestSchema:

    @pytest.mark.parametrize('field', FIELDS)
    def test_latest_fields_are_read_only_and_nullable(self, field):
        declared = LatestMeasurementSerializer().fields[field]
        assert declared.read_only and declared.allow_null
