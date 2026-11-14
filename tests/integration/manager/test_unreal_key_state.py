# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the key state on a table of record states, and the
strategy hooks (Unreal foundation FR-PM-1, FR-PM-2 steps 1-4, FR-MR-8,
FR-T-4).
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from shared.unreal.validation import overrides_sha256

from workers.models import Job, UnrealMeasurement
from workers.renderers import get_strategy
from workers.services.unreal_pilot import ProjectLockMissing, Target, ensure_pilot, key_state, key_state_of

from .unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset

TARGET = Target(BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
                RENDER_SETTINGS['unreal.preset'])
EMPTY = overrides_sha256({})


def _record(asset, *, status=None, peak=None, error='', overrides=EMPTY, age=0):
    pilot = None
    if status is not None:
        pilot = Job.objects.create(
            name=f'sethlans-pilot-x{UnrealMeasurement.objects.count()}', asset=asset, unreal_kind='pilot',
            output_file_pattern='//p/p.png', render_settings=dict(RENDER_SETTINGS), status=status,
            error_message=error)
    return UnrealMeasurement.objects.create(
        project=asset.project, asset=asset, overrides_sha256=overrides, target_sha256=TARGET.sha256(),
        engine_build=BUILD, map=TARGET.map, sequence=TARGET.sequence, preset=TARGET.preset,
        pilot_job=pilot, peak_mb=peak, created_at=timezone.now() - timedelta(minutes=age),
        measured_at=timezone.now() if peak else None)


@pytest.fixture
def asset(db):
    return unreal_asset()


def _state(asset):
    return key_state_of(asset.project, asset.pk, EMPTY, TARGET.sha256())


@pytest.mark.django_db
class TestKeyStates:

    def test_none(self, asset):
        assert _state(asset).state == 'none'

    @pytest.mark.parametrize('status', ['QUEUED', 'RENDERING'])
    def test_pending(self, asset, status):
        _record(asset, status=status)
        assert _state(asset).state == 'pending'

    @pytest.mark.parametrize('status, error, reason', [
        ('ERROR', 'boom', 'boom'), ('CANCELED', '', 'Pilot job was canceled.'),
        ('DONE', '', 'Pilot job finished without a measurement.'), (None, '', 'Pilot job was deleted.'),
    ])
    def test_failed(self, asset, status, error, reason):
        _record(asset, status=status, error=error)
        found = _state(asset)
        assert (found.state, found.reason) == ('failed', reason)

    def test_measured_uses_the_latest_successful_peak(self, asset):
        _record(asset, status='DONE', peak=7000, age=10)
        _record(asset, status='DONE', peak=8000, age=5)
        _record(asset, status='QUEUED')  # a pending re-measurement does not unmeasure the key
        found = _state(asset)
        assert (found.state, found.peak_mb) == ('measured', 8000)

    def test_latest_record_decides_without_success(self, asset):
        _record(asset, status='ERROR', error='old', age=10)
        _record(asset, status='QUEUED', age=1)
        assert _state(asset).state == 'pending'

    def test_other_cvars_are_another_key(self, asset):
        _record(asset, status='DONE', peak=8000, overrides='f' * 64)
        assert _state(asset).state == 'none'

    def test_ensure_pilot_only_for_none(self, asset):
        with transaction.atomic():
            get_strategy('UNREAL').lock_for_write(asset.project)
            assert ensure_pilot(asset.project, asset, TARGET, reason='t') is not None
        Job.objects.filter(unreal_kind='pilot').update(status='ERROR')
        with transaction.atomic():
            get_strategy('UNREAL').lock_for_write(asset.project)
            assert ensure_pilot(asset.project, asset, TARGET, reason='t') is None

    def test_key_state_of_a_job(self, asset):
        job = Job.objects.create(name='StateJob', asset=asset, output_file_pattern='//r/f.png',
                                 render_settings=dict(RENDER_SETTINGS))
        assert key_state(job).state == 'none'
        _record(asset, status='DONE', peak=8100)
        assert (key_state(job).state, key_state(job).peak_mb) == ('measured', 8100)


@pytest.mark.django_db
class TestStrategyHooks:

    def test_blender_hooks_are_no_ops(self, project):
        strategy = get_strategy('BLENDER')
        assert strategy.lock_for_write(project) is None
        assert strategy.project_measurement(project, {}) is None
        assert not strategy.supports('pilot_measurement')
        for hook, args in (('after_job_create', (None,)), ('after_job_update', (None, {'render_settings'})),
                           ('after_asset_create', (None,)), ('after_project_update', (project, {'x'}))):
            assert getattr(strategy, hook)(*args) is None

    def test_unreal_supports_pilot_measurement_only(self):
        strategy = get_strategy('UNREAL')
        assert strategy.supports('pilot_measurement')
        assert not strategy.supports('tiled_job') and not strategy.supports('animation')

    def test_lock_for_write_returns_the_re_read_row(self, asset):
        stale = asset.project
        type(stale).objects.filter(pk=stale.pk).update(name='ChangedName')
        with transaction.atomic():
            locked = get_strategy('UNREAL').lock_for_write(stale)
        assert locked.name == 'ChangedName' and stale.name != 'ChangedName'


@pytest.mark.django_db(transaction=True)
def test_lock_for_write_needs_a_transaction():
    asset = unreal_asset()
    with pytest.raises(RuntimeError):
        get_strategy('UNREAL').lock_for_write(asset.project)


@pytest.mark.django_db(transaction=True)
def test_a_lock_does_not_outlive_its_transaction():
    """A decorated function reuses one Atomic; its second call has no lock."""
    asset = unreal_asset()

    @transaction.atomic
    def _ensure(lock):
        if lock:
            get_strategy('UNREAL').lock_for_write(asset.project)
        return ensure_pilot(asset.project, asset, TARGET, reason='t')

    assert _ensure(True) is not None
    Job.objects.filter(unreal_kind='pilot').update(status='ERROR')
    with pytest.raises(ProjectLockMissing):
        _ensure(False)


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_lock_does_not_count():
    asset = unreal_asset()
    with transaction.atomic():
        try:
            with transaction.atomic():
                get_strategy('UNREAL').lock_for_write(asset.project)
                raise ValueError('roll back the savepoint')
        except ValueError:
            pass
        with pytest.raises(ProjectLockMissing):
            ensure_pilot(asset.project, asset, TARGET, reason='t')
