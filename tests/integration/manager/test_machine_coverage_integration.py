# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: one machine's result covering a smaller or draft key
(``services/machine_coverage``; Unreal per-machine memory test D8, D15,
FR-MT-P-1, FR-MT-P-2).

Covering record **R** is made the way a real pilot leaves it: created by
``create_pilot`` for a sequence target (its ``measured_ranges`` are the
ranges whose start frame the pilot rendered), then measured ``fits``.

Covers: a key whose ranges are a subset of R's gets a derived record (no
pilot; ``verdict_source='covered'``, ``covered_by=R``, every copied
figure, the driver and R's ranges) and reads ``fits``; a draft is covered
by the full settings; a derived record replaces the pair's ``retest_due``
record; a sampled pilot covers only the shots it rendered; a partial
overlap, another size, other CVars, a draft R, a stale, superseded,
untested or does_not_fit R give a pilot; a derived record never covers;
a standalone key matches by exact key only; R covers only its own worker
and card kind; of several covering records the newest is used; R turned
non-current, stale or does_not_fit between the query and ``derive`` gives
a pilot and no derived record.
"""

import pytest

from workers.models import Project, UnrealMeasurement
from workers.services import machine_tests
from workers.services.machine_coverage import COPIED_FIELDS, covering_records, needed_ranges
from workers.services.machine_tests import pair_states
from workers.services.unreal_pilot import Target

from .machine_test_helpers import KIND_AMD, TARGET, current_of, kinds_worker, locked_create, locked_ensure, pair_record
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, measure, ready_worker

RANGES = ((0, 4), (4, 10), (20, 24))
SIZE = (64, 36)
DRAFT = (('motion_blur', False), ('temporal_samples', 2))


def seq(shot_set='a', ranges=RANGES, frames=None, draft=(), size=SIZE):
    """A sequence target; its pilot renders the start frame of every range unless ``frames`` is given."""
    frames = tuple(start for start, _end in ranges) if frames is None else frames
    return Target(TARGET.engine_build, TARGET.map, TARGET.sequence, TARGET.preset, size, shot_set * 64,
                  frames, draft, ranges)


FULL = seq()
SUBSET = seq('b', ((0, 4),))


def covering(asset, worker, target=FULL, kind=KIND, **update):
    """R: ``worker``'s pilot record of ``target`` measured ``fits`` (``update``: fields changed afterwards)."""
    record = locked_create(asset, target, worker, kind)
    measure(record, peak_mb=9100)
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        driver_version='32.0.15.6094', job_peak_max_mb=9300, baseline_used_mb=945, gpu_total_mb=32607,
        ram={'render': {'ws_mb': 7100, 'commit_mb': 20700}}, **update)
    return UnrealMeasurement.objects.get(pk=record.pk)


def _derived(asset, target, worker, kind=KIND):
    return UnrealMeasurement.objects.filter(asset=asset, target_sha256=target.sha256(), worker=worker,
                                            card_kind=kind, verdict_source='covered')


@pytest.fixture
def setup(db, worker_with_token):
    return unreal_asset(), ready_worker(worker_with_token[0])


@pytest.mark.django_db
class TestCovered:

    def test_a_subset_gets_a_derived_record(self, setup):
        asset, worker = setup
        record = covering(asset, worker)
        assert record.measured_ranges == [list(pair) for pair in RANGES]
        assert locked_ensure(asset, SUBSET) == []
        [derived] = _derived(asset, SUBSET, worker)
        assert (derived.pilot_job_id, derived.covered_by_id, derived.current) == (None, record.pk, True)
        assert derived.base_target_sha256 == record.base_target_sha256 == SUBSET.base_sha256()
        assert {field: getattr(derived, field) for field in COPIED_FIELDS} == {
            field: getattr(record, field) for field in COPIED_FIELDS}
        assert (derived.verdict, derived.driver_version, derived.peak_mb) == ('fits', '32.0.15.6094', 9100)
        assert pair_states(asset, asset.project.unreal_overrides_sha256, SUBSET.sha256(),
                           [(worker, KIND)]) == {(worker.pk, KIND): 'fits'}
        assert UnrealMeasurement.objects.get(pk=record.pk).current is True

    def test_a_draft_is_covered_by_the_full_settings(self, setup):
        asset, worker = setup
        record = covering(asset, worker)
        drafted = seq('a', draft=DRAFT)
        assert locked_ensure(asset, drafted) == []
        assert _derived(asset, drafted, worker).get().covered_by_id == record.pk

    def test_a_derived_record_replaces_a_retest_due_one(self, setup):
        asset, worker = setup
        covering(asset, worker)
        old = pair_record(asset, SUBSET.sha256(), worker, verdict='fits', stale='driver_changed')
        assert locked_ensure(asset, SUBSET) == []
        assert current_of(asset, SUBSET.sha256(), worker) == [_derived(asset, SUBSET, worker).get().pk]
        assert UnrealMeasurement.objects.get(pk=old.pk).current is False

    def test_a_sampled_pilot_covers_only_the_shots_it_rendered(self, setup):
        asset, worker = setup
        record = covering(asset, worker, seq(frames=(0, 20)))
        assert record.measured_ranges == [[0, 4], [20, 24]]
        [pilot] = locked_ensure(asset, seq('c', ((4, 10),)))
        assert pilot.target_worker_id == worker.pk
        assert locked_ensure(asset, seq('d', ((0, 4), (20, 24)))) == []

    def test_the_newest_covering_record_is_used(self, setup):
        asset, worker = setup
        covering(asset, worker)
        newer = covering(asset, worker, seq('e', RANGES + ((30, 34),)))
        found = covering_records(asset, asset.project.unreal_overrides_sha256, SUBSET, needed_ranges(SUBSET),
                                 [(worker, KIND)])
        assert found == {(worker.pk, KIND): newer}


@pytest.mark.django_db
class TestNotCovered:

    @pytest.mark.parametrize('target', [
        seq('f', ((0, 4), (30, 34))), seq('g', ((0, 4),), size=(128, 72)), TARGET,
    ], ids=['partial_overlap', 'other_size', 'standalone'])
    def test_the_key_gets_a_pilot(self, setup, target):
        asset, worker = setup
        # R at SIZE (at no size for the standalone key, so both share the base key).
        covering(asset, worker, seq(size=None if target.resolution is None else SIZE))
        assert len(locked_ensure(asset, target)) == 1
        assert not _derived(asset, target, worker).exists()

    @pytest.mark.parametrize('update', [
        {'stale_reason': 'driver_changed'}, {'verdict': 'does_not_fit'}, {'current': False}, {'verdict': ''},
    ], ids=['stale', 'does_not_fit', 'superseded', 'untested'])
    def test_r_in_another_state_covers_nothing(self, setup, update):
        asset, worker = setup
        covering(asset, worker, **update)
        assert len(locked_ensure(asset, SUBSET)) == 1

    def test_a_draft_r_covers_nothing(self, setup):
        asset, worker = setup
        covering(asset, worker, seq('a', draft=DRAFT))
        assert len(locked_ensure(asset, SUBSET)) == 1
        assert len(locked_ensure(asset, seq('b', ((0, 4),), draft=DRAFT))) == 1

    def test_other_cvars_cover_nothing(self, setup):
        asset, worker = setup
        covering(asset, worker)
        Project.objects.filter(pk=asset.project_id).update(unreal_overrides_sha256='f' * 64)
        asset.project.refresh_from_db()
        assert len(locked_ensure(asset, SUBSET)) == 1

    def test_a_derived_record_never_covers(self, setup):
        asset, worker = setup
        record = covering(asset, worker)
        assert locked_ensure(asset, SUBSET) == []
        UnrealMeasurement.objects.filter(pk=record.pk).update(current=False)
        # The derived record still holds R's three ranges, yet it has no pilot of its own.
        assert len(locked_ensure(asset, seq('h', ((4, 10),)))) == 1

    def test_only_the_same_worker_and_kind(self, setup, make_worker_client):
        asset, worker = setup
        kinds_worker(worker, KIND, KIND_AMD)
        other = ready_worker(make_worker_client('mt-cov-b')[0])
        record = covering(asset, worker)
        made = locked_ensure(asset, SUBSET)
        assert sorted((p.target_worker_id, p.render_settings['unreal.card_kind']) for p in made) == sorted(
            [(worker.pk, KIND_AMD), (other.pk, KIND)])
        assert _derived(asset, SUBSET, worker).get().covered_by_id == record.pk


@pytest.mark.django_db
class TestChangedBeforeDerive:

    @pytest.mark.parametrize('change', [
        {'current': False}, {'stale_reason': 'driver_changed'}, {'verdict': 'does_not_fit'},
    ], ids=['superseded', 'stale', 'does_not_fit'])
    def test_r_changed_after_the_query_gives_a_pilot(self, setup, mocker, change):
        asset, worker = setup
        record = covering(asset, worker)
        real = machine_tests.covering_records

        def query_then_change(*args):
            found = real(*args)
            assert found[(worker.pk, KIND)].pk == record.pk
            UnrealMeasurement.objects.filter(pk=record.pk).update(**change)
            return found

        mocker.patch.object(machine_tests, 'covering_records', side_effect=query_then_change)
        [pilot] = locked_ensure(asset, SUBSET)
        assert pilot.target_worker_id == worker.pk
        assert not _derived(asset, SUBSET, worker).exists()
        assert current_of(asset, SUBSET.sha256(), worker) == [UnrealMeasurement.objects.get(pilot_job=pilot).pk]
