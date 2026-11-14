# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-UE-25 rejection boundaries and the CSV capture's hardening (chunk 7b
review: code M2/L3/L6, security W3/W4).
"""
import csv
import os

import pytest

from sethlans_worker_agent.unreal_executor import pilot, pilot_csv
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from tests.unit.worker.unreal._executor_fakes import phase_run, trace


class TestRuleBoundaries:

    def test_freed_memory_513_releases_512_does_not(self):
        ok = phase_run('render', {0: 3000}, {0: 3000}, trace({0: 9000}, {0: 2488}))   # exactly 512 below
        assert pilot.measure([ok], None).peak_mb == 6000
        bad = phase_run('render', {0: 3000}, {0: 3000}, trace({0: 9000}, {0: 2487}))  # 513 below
        with pytest.raises(LocalConflict, match='freed'):
            pilot.measure([bad], None)

    def test_sampling_gap_10_passes_10_5_releases(self):
        assert pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 9000}, gap=10.0))], None)
        with pytest.raises(LocalConflict, match='stalled'):
            pilot.measure([phase_run('render', {0: 1000}, {0: 1000}, trace({0: 9000}, gap=10.5))], None)

    def test_all_zero_deltas_are_an_implausible_peak(self):
        runs = [phase_run(phase, {0: 1000}, {0: 1000}, trace({0: 1000})) for phase in ('prep', 'warmup', 'render')]
        with pytest.raises(JobFailure, match=r'implausible GPU memory peak \(0 MB\)'):
            pilot.measure(runs, None)


def _profile(tmp_path):
    profile = tmp_path / 'ueprofile'
    pilot_csv.csv_dir(profile).mkdir(parents=True)
    return profile


def _write(path, rows):
    path.write_text('EventsCol,GPUMem/LocalUsedMB,Other\n' + ''.join(rows), encoding='utf-8')


class TestCsvHardening:

    @pytest.mark.parametrize('bad', ['inf', '-inf', 'nan', '1e400', '-5', '2000000'])
    def test_non_finite_or_out_of_range_values_are_ignored(self, tmp_path, bad):
        path = tmp_path / 'a.csv'
        _write(path, [f'x,{bad},1\n', 'x,8102,1\n'])
        assert pilot_csv.peak_local_used_mb(path) == 8102
        _write(path, [f'x,{bad},1\n'])
        assert pilot_csv.peak_local_used_mb(path) is None

    def test_an_oversized_field_ends_the_scan_but_keeps_the_rows_read(self, tmp_path):
        before = csv.field_size_limit()
        path = tmp_path / 'a.csv'
        _write(path, ['x,7000,1\n', 'x,7500,' + 'y' * (before + 10) + '\n', 'x,9999,1\n'])
        assert pilot_csv.peak_local_used_mb(path) == 7000
        assert csv.field_size_limit() == before  # the global limit is never changed

    def test_collect_survives_parse_errors(self, tmp_path, monkeypatch):
        profile = _profile(tmp_path)
        _write(pilot_csv.csv_dir(profile) / 'new.csv', ['x,1,1\n'])
        monkeypatch.setattr(pilot_csv, 'peak_local_used_mb', lambda path: float('inf') + int('x'))
        log = tmp_path / 'render.log'
        log.write_text('nothing\n', encoding='utf-8')
        assert pilot_csv.collect(log, profile, frozenset(), tmp_path) is None

    def test_oversized_csv_is_ignored(self, tmp_path, monkeypatch):
        profile = _profile(tmp_path)
        _write(pilot_csv.csv_dir(profile) / 'big.csv', ['x,5000,1\n'])
        monkeypatch.setattr(pilot_csv, 'MAX_CSV_BYTES', 10)
        log = tmp_path / 'render.log'
        log.write_text('nothing\n', encoding='utf-8')
        assert pilot_csv.collect(log, profile, frozenset(), tmp_path) is None
        assert (pilot_csv.csv_dir(profile) / 'big.csv').exists()  # not moved

    def test_a_link_on_the_folder_chain_gives_no_csv(self, tmp_path):
        profile = tmp_path / 'ueprofile'
        elsewhere = tmp_path / 'artist' / 'CSV'
        elsewhere.mkdir(parents=True)
        _write(elsewhere / 'theirs.csv', ['x,5000,1\n'])
        saved = pilot_csv.csv_dir(profile).parent.parent   # ...\5.8\Saved
        saved.mkdir(parents=True)
        try:
            _junction(elsewhere.parent, saved / 'Profiling')
        except OSError as exc:
            pytest.skip(f'no unprivileged directory link here: {exc}')
        assert pilot_csv.safe_csv_dir(profile) is None and pilot_csv.listing(profile) == frozenset()
        log = tmp_path / 'render.log'
        log.write_text('nothing\n', encoding='utf-8')
        assert pilot_csv.collect(log, profile, frozenset(), tmp_path) is None
        assert (elsewhere / 'theirs.csv').exists()

    def test_a_csv_that_is_a_link_is_ignored(self, tmp_path):
        profile = _profile(tmp_path)
        target = tmp_path / 'target.csv'
        _write(target, ['x,5000,1\n'])
        try:
            os.symlink(target, pilot_csv.csv_dir(profile) / 'link.csv')
        except OSError as exc:
            pytest.skip(f'file symlinks need a privilege here: {exc}')
        log = tmp_path / 'render.log'
        log.write_text('nothing\n', encoding='utf-8')
        assert pilot_csv.collect(log, profile, frozenset(), tmp_path) is None and target.exists()

    def test_a_file_where_a_folder_belongs_gives_no_csv(self, tmp_path):
        profile = tmp_path / 'ueprofile'
        csv_dir = pilot_csv.csv_dir(profile)
        csv_dir.parent.mkdir(parents=True)
        csv_dir.write_text('not a folder', encoding='utf-8')
        assert pilot_csv.safe_csv_dir(profile) is None


def _junction(target, link):
    """A directory junction on Windows (unprivileged), a symlink elsewhere."""
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)
