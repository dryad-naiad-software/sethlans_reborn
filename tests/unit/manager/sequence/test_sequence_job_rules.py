# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the manager-created job kinds of Unreal sequence rendering
(spec D3, D12, D20, FR-SD-3, FR-SD-9).

Covers: the refusal texts and which kinds they apply to
(``job_kind_rules``), the ungated poll and claim rule for ``shots`` jobs
(``renderers/unreal_sequence``), the reserved ``sethlans-seq-`` name
prefix, the D20 job name, and why a discovery fails for its job
(``failure_reason``).  No database.
"""

from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from workers.constants import UnrealKind
from workers.constants_sequence import (
    SHOTS_JOB_CANCELED, SHOTS_JOB_DELETED, SHOTS_JOB_DONE_WITHOUT_RESULT, SHOTS_JOB_FAILED, shots_job_name,
)
from workers.renderers.unreal_sequence import skips_measured_gate, ungated_poll_q
from workers.renderers.unreal_settings import validate_unreal_job
from workers.services.job_kind_rules import (
    done_only_by_result, never_requeued, pilot_done_message, pilot_requeue_message,
)
from workers.services.sequence_discovery import MAX_REASON, failure_reason


def _job(kind, pk=42, project_id=7):
    return SimpleNamespace(unreal_kind=kind, pk=pk, asset=SimpleNamespace(project_id=project_id))


class TestJobKindRules:
    @pytest.mark.parametrize('kind, text', [
        (UnrealKind.PILOT, "Pilot jobs complete through POST /api/jobs/42/pilot-result/."),
        (UnrealKind.SHOTS, "Shots jobs complete through POST /api/jobs/42/shots-result/."),
        (UnrealKind.AUDIO, "Audio jobs complete through POST /api/jobs/42/sound-result/."),
    ])
    def test_done_message_names_the_result_endpoint(self, kind, text):
        assert done_only_by_result(_job(kind))
        assert pilot_done_message(_job(kind)) == text

    def test_render_jobs_finish_by_patch(self):
        assert not done_only_by_result(_job(UnrealKind.RENDER))
        assert not never_requeued(_job(UnrealKind.RENDER))

    def test_pilot_and_shots_are_never_requeued(self):
        assert never_requeued(_job(UnrealKind.PILOT)) and never_requeued(_job(UnrealKind.SHOTS))
        assert not never_requeued(_job(UnrealKind.AUDIO))

    def test_requeue_messages(self):
        assert pilot_requeue_message(_job(UnrealKind.SHOTS)) == (
            "Read the shots again with POST /api/sequence-discoveries/.")
        assert pilot_requeue_message(_job(UnrealKind.PILOT)) == (
            "Use POST /api/projects/7/unreal-measurement/retry/ to measure again.")


class TestUngatedKinds:
    def test_only_shots_skip_the_gate(self):
        assert skips_measured_gate(_job(UnrealKind.SHOTS))
        for kind in (UnrealKind.RENDER, UnrealKind.PILOT, UnrealKind.AUDIO):
            assert not skips_measured_gate(_job(kind))

    def test_poll_q_names_the_shots_kind(self):
        assert ungated_poll_q().children == [('unreal_kind__in', (UnrealKind.SHOTS,))]


class TestNames:
    def test_d20_name_fits_the_job_name_field(self):
        assert shots_job_name(12) == 'sethlans-seq-shots-12'
        assert len(shots_job_name(2 ** 63 - 1)) <= 40

    @pytest.mark.parametrize('name', ['sethlans-seq-shots-1', 'SETHLANS-SEQ-x', 'sethlans-seq-'])
    def test_reserved_prefix_refused(self, name):
        attrs = {'name': name}
        with pytest.raises(ValidationError) as info:
            validate_unreal_job(attrs, SimpleNamespace(name='other'))
        assert 'reserved for sequence renders' in str(info.value.detail['name'][0])

    def test_unchanged_name_is_not_rechecked(self):
        instance = SimpleNamespace(name='sethlans-seq-shots-3')
        assert validate_unreal_job({'name': 'sethlans-seq-shots-3'}, instance) == {'name': 'sethlans-seq-shots-3'}


class TestFailureReason:
    def test_deleted(self):
        assert failure_reason(None) == SHOTS_JOB_DELETED

    def test_error_keeps_the_job_message(self):
        assert failure_reason({'status': 'ERROR', 'error_message': ' no shot track or camera cut found '}) == (
            'no shot track or camera cut found')

    def test_error_message_loses_nul_and_control_characters(self):
        assert failure_reason({'status': 'ERROR', 'error_message': 'bad\u0000 shot\x1b'}) == 'bad shot'

    def test_error_without_message(self):
        assert failure_reason({'status': 'ERROR', 'error_message': ''}) == SHOTS_JOB_FAILED

    def test_error_message_capped(self):
        assert len(failure_reason({'status': 'ERROR', 'error_message': 'x' * 5000})) == MAX_REASON

    def test_canceled_and_done(self):
        assert failure_reason({'status': 'CANCELED', 'error_message': ''}) == SHOTS_JOB_CANCELED
        assert failure_reason({'status': 'DONE', 'error_message': ''}) == SHOTS_JOB_DONE_WITHOUT_RESULT

    @pytest.mark.parametrize('status', ['QUEUED', 'RENDERING'])
    def test_active_job_is_no_failure(self, status):
        assert failure_reason({'status': status, 'error_message': ''}) is None
