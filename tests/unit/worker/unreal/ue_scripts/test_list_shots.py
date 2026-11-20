# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shot discovery's shot list on the UE side (sequence rendering D4, FR-SD-4..6, AC-3): the prep script in mode
``list_shots`` against the stub ``unreal`` module. Sequence and shot names are made up.
"""
import importlib.util
import random
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal.ue_scripts._discovery import run_list, shots_of

SHOTS = ue.MovieSceneCinematicShotTrack
CUTS = ue.MovieSceneCameraCutTrack
OVERLAP = "Shot sections {a} and {b} overlap; Sethlans cannot tell which one renders."
MUTE = "Mute and disabled state of shot tracks is not read; muted shots are planned."


class TestShotTracks:

    def test_one_shot_per_enabled_section_clipped_to_its_inner_cuts(self, tmp_path, monkeypatch):
        sections = [ue.shot(0, 100, [(10, 50)], name='sh010'),               # cut covers edit 10..49
                    ue.shot(100, 241, [(101, 250)], offset_frames=101, name='sh020'),  # covers the whole section
                    ue.shot(241, 300, [(0, 100)], name='sh030', active=False)]  # disabled: not a shot
        source, shots, warnings = shots_of(tmp_path, monkeypatch, [SHOTS(sections)])
        assert source == 'shot_tracks' and warnings == [MUTE]
        assert shots == [['sh010', 10, 50], ['sh020', 100, 241]]

    def test_a_hole_in_the_coverage_makes_parts_and_touching_cuts_do_not(self, tmp_path, monkeypatch):
        sections = [ue.shot(0, 100, [(0, 20), (40, 60), (60, 70)], name='sh010'),
                    ue.shot(100, 200, [(0, 30), (50, 60), (80, 90)], name='sh020')]
        _source, shots, _warnings = shots_of(tmp_path, monkeypatch, [SHOTS(sections)])
        assert shots == [['sh010', 0, 20], ['sh010-part2', 40, 70],
                         ['sh020', 100, 130], ['sh020-part2', 150, 160], ['sh020-part3', 180, 190]]

    def test_a_section_without_coverage_is_dropped_with_a_warning(self, tmp_path, monkeypatch):
        sections = [ue.shot(0, 100, [(0, 100)], name='sh010'), ue.shot(100, 200, [(500, 600)], name='sh020'),
                    ue.shot(200, 300, [], name='sh030')]
        _source, shots, warnings = shots_of(tmp_path, monkeypatch, [SHOTS(sections)])
        assert shots == [['sh010', 0, 100]]
        assert warnings == [MUTE] + [f"Shot {n} has no camera cut inside the playback range; its frames are not "
                                     "rendered." for n in ('sh020', 'sh030')]

    def test_shots_are_clipped_to_the_playback_range(self, tmp_path, monkeypatch):
        sections = [ue.shot(-50, 30, [(0, 200)], name='early'), ue.shot(30, 80, [(0, 200)], name='mid'),
                    ue.shot(80, 400, [(0, 400)], name='late'), ue.shot(500, 600, [(0, 100)], name='outside')]
        source, shots, warnings = shots_of(tmp_path, monkeypatch, [SHOTS(sections)], start=0, end=120)
        assert (source, warnings) == ('shot_tracks', [MUTE])  # a section outside the range is simply not rendered
        assert shots == [['early', 0, 30], ['mid', 30, 80], ['late', 80, 120]]

    def test_two_overlapping_sections_fail_naming_both(self, tmp_path, monkeypatch):
        sections = [ue.shot(0, 100, [(0, 100)], name='sh010'), ue.shot(90, 200, [(0, 200)], name='sh020_alt')]
        error = shots_of(tmp_path, monkeypatch, [SHOTS(sections[:1]), SHOTS(sections[1:])])  # two rows
        assert error == OVERLAP.format(a='sh010', b='sh020_alt')

    def test_sections_that_overlap_outside_their_cuts_are_refused_too(self, tmp_path, monkeypatch):
        """Domain review B1: Sequencer renders only the top row where rows overlap, whatever the cuts, so frames
        40..49 of A would render nothing (B is on top there and its cuts start at 50)."""
        a = ue.shot(0, 100, [(0, 50)], name='A')
        b = ue.shot(40, 100, [(10, 60)], name='B', row=1)
        assert shots_of(tmp_path, monkeypatch, [SHOTS([a, b])]) == OVERLAP.format(a='A', b='B')

    def test_sections_that_overlap_only_outside_the_playback_range_are_fine(self, tmp_path, monkeypatch):
        tracks = [SHOTS([ue.shot(0, 100, [(0, 100)], name='a'), ue.shot(80, 200, [(0, 120)], name='b', row=1)])]
        assert shots_of(tmp_path, monkeypatch, tracks, start=100, end=200)[1] == [['b', 100, 200]]

    def test_mute_state_is_not_read_and_one_warning_says_so(self, tmp_path, monkeypatch):
        """Track mute and row eval-disabled state are not readable from Python in UE 5.8.4 (probe): muted shots are
        planned, and every shot-track discovery carries one warning."""
        sections = [ue.shot(0, 100, [(0, 100)], name='a'), ue.shot(100, 200, [(0, 100)], name='b', row=1)]
        assert shots_of(tmp_path, monkeypatch, [SHOTS(sections)])[1:] == ([['a', 0, 100], ['b', 100, 200]], [MUTE])
        assert not hasattr(ue.Track([]), 'is_eval_disabled')  # the stub has no accessor the engine lacks

    @pytest.mark.parametrize('scale,error', [
        (ue.TimeWarp(1.0), None),
        (ue.TimeWarp(0.5), "Shot slow plays at a time scale other than 1; Sethlans needs 1."),
        (ue.TimeWarp(2.0), "Shot slow plays at a time scale other than 1; Sethlans needs 1."),
        (ue.TimeWarp(broken=True), "The time scale of shot slow could not be read; Sethlans needs 1."),
        # A curve warp reads as 1.0 in 5.8.4 (the engine logs an error); the variant has no "is fixed" check, so
        # a curve is planned as 1. Known limit, see sethlans_shots._play_rate.
        (ue.TimeWarp(curve=True), None),
    ])
    def test_the_time_scale(self, tmp_path, monkeypatch, scale, error):
        sections = [ue.shot(0, 100, [(0, 100)], name='a'), ue.shot(100, 200, [(0, 100)], name='slow', time_scale=scale)]
        got = shots_of(tmp_path, monkeypatch, [SHOTS(sections)])
        if error is None:
            assert got[1] == [['a', 0, 100], ['slow', 100, 200]]
        else:
            assert got == error

    def test_cut_tracks_that_are_not_read_are_named_in_warnings(self, tmp_path, monkeypatch):
        """Domain should-fix 1: MRQ also takes cuts from nested subsequences and the sequence's own cut track. A
        nested subsequence without cuts of its own (an animation layer) gives no warning."""
        nested = [ue.MovieSceneSubTrack([ue.shot(0, 10, [(0, 10)], name='inner')])]
        no_cuts = [ue.MovieSceneSubTrack([ue.Section(0, 10, sequence=ue.Sequence(0, 10, [], name='anim'))])]
        tracks = [SHOTS([ue.shot(0, 100, [(0, 100)], name='a', inner_tracks=nested),
                         ue.shot(100, 200, [(0, 100)], name='b', inner_tracks=no_cuts)]), CUTS([ue.Section(0, 100)])]
        _source, shots, warnings = shots_of(tmp_path, monkeypatch, tracks)
        assert shots == [['a', 0, 100], ['b', 100, 200]]
        assert warnings == ["Shot a has subsequences with camera cuts; those cuts are not read.", MUTE,
                            "The sequence has camera cuts of its own beside its shot tracks; they are not read."]

    def test_subsequence_tracks_are_not_shots(self, tmp_path, monkeypatch):
        tracks = [ue.MovieSceneSubTrack([ue.shot(0, 100, [(0, 100)], name='fx')]), CUTS([ue.Section(0, 60)])]
        assert shots_of(tmp_path, monkeypatch, tracks)[:2] == ('camera_cuts', [['cut_001', 0, 60]])

    def test_a_faster_inner_sequence_maps_by_its_rate(self, tmp_path, monkeypatch):
        sub = ue.Sequence(0, 10000, [CUTS([ue.Section(11, 51)])], rate=48, ticks=48000, name='fast')
        _source, shots, _warnings = shots_of(tmp_path, monkeypatch, [SHOTS([ue.Section(0, 100, sequence=sub)])])
        assert shots == [['fast', 6, 26]]  # inner 2f: 2f >= 11 from f = 6; 2f < 51 up to f = 25

    def test_names_are_cleaned_on_the_ue_side_too(self, tmp_path, monkeypatch):
        long = 'x' * 70
        sections = [ue.shot(0, 10, [(0, 10)], name='sh 010/ä'), ue.shot(10, 20, [(0, 3), (5, 10)], name=long)]
        shots = shots_of(tmp_path, monkeypatch, [SHOTS(sections)])[1]
        assert [row[0] for row in shots] == ['sh_010__', 'x' * 56, 'x' * 56 + '-part2']


class TestCameraCuts:

    def test_no_shot_track_gives_the_top_level_cuts(self, tmp_path, monkeypatch):
        cuts = [ue.Section(-5, 3), ue.Section(3, 10), ue.Section(20, 25, active=False), ue.Section(40, 50),
                ue.Section(90, 120)]
        source, shots, warnings = shots_of(tmp_path, monkeypatch, [CUTS(cuts)], start=0, end=100)
        assert (source, warnings) == ('camera_cuts', [])
        assert shots == [['cut_001', 0, 3], ['cut_002', 3, 10], ['cut_003', 40, 50], ['cut_004', 90, 100]]

    def test_a_shot_track_with_only_disabled_sections_falls_back_to_the_cuts(self, tmp_path, monkeypatch):
        tracks = [SHOTS([ue.shot(0, 100, [(0, 100)], active=False)]), CUTS([ue.Section(0, 100)])]
        assert shots_of(tmp_path, monkeypatch, tracks)[:2] == ('camera_cuts', [['cut_001', 0, 100]])

    def test_overlapping_cuts_fail(self, tmp_path, monkeypatch):
        error = shots_of(tmp_path, monkeypatch, [CUTS([ue.Section(0, 50)]), CUTS([ue.Section(40, 60)])])
        assert error == "Camera cuts cut_001 and cut_002 overlap; Sethlans does not support blended camera cuts yet."

    @pytest.mark.parametrize('tracks', [[], [CUTS([ue.Section(2000, 2100)])], [CUTS([ue.Section(0, 10, active=False)])]])
    def test_neither_fails_the_discovery(self, tmp_path, monkeypatch, tracks):
        error = shots_of(tmp_path, monkeypatch, tracks)
        assert 'no shot track or camera cut found' in error

    def test_shot_sections_without_any_coverage_fail(self, tmp_path, monkeypatch):
        error = shots_of(tmp_path, monkeypatch, [SHOTS([ue.shot(0, 100, [(500, 600)], name='sh010')])])
        assert error == "Shot discovery failed: no shot section has a camera cut inside the playback range."


class TestResult:

    def test_rate_range_and_nothing_written(self, tmp_path, monkeypatch):
        seq = ue.Sequence(0, 720, [CUTS([ue.Section(0, 720)])], rate=(30000, 1001))
        result, error, world = run_list(tmp_path, monkeypatch, ue.graph(), seq)
        assert error is None and result['ok'] is True
        assert result['frame_rate'] == [30000, 1001] and result['playback_range'] == [0, 720]
        assert result['preset_kind'] == 'graph' and 'queue_asset' not in result
        assert world['queues'] == [] and world['saved'] == [] and world['searched'] is True  # no queue, no save

    def test_more_shots_than_allowed_fail_without_writing_the_list(self, tmp_path, monkeypatch):
        seq = ue.Sequence(0, 300, [CUTS([ue.Section(i, i + 1) for i in range(5)])])
        result, error, _world = run_list(tmp_path, monkeypatch, ue.graph(), seq, max_shots=4)
        assert error is not None and result['error'] == 'Sequence has 5 shots; at most 4 are supported.'
        assert 'shots' not in result

    def test_a_subgraph_preset_fails_as_the_prep_does(self, tmp_path, monkeypatch):
        seq = ue.Sequence(0, 100, [CUTS([ue.Section(0, 100)])])
        result, error, _world = run_list(tmp_path, monkeypatch, ue.graph(subgraphs=['/Game/Sub']), seq)
        assert error is not None and result['error'] == 'Preset uses subgraphs; phase 1 does not rewrite subgraph outputs.'


@pytest.fixture()
def shots_module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal({'assets': {}}))
    spec = importlib.util.spec_from_file_location('sethlans_shots_test', job_dir.SCRIPTS_SOURCE / job_dir.SHOTS_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_interval_arithmetic_agrees_with_the_per_frame_rule(shots_module):
    """D4: the coverage computed without a frame loop equals ``in_shot_cut`` frame by frame (random sections)."""
    rng = random.Random(7)
    for _ in range(300):
        cuts = sorted({(a, a + rng.randint(1, 40)) for a in rng.sample(range(0, 200), rng.randint(0, 4))})
        start = rng.randint(-20, 100)
        inner_rate = rng.choice([24, 25, 30, 48, 60])
        sub = ue.Sequence(0, 10000, [CUTS([ue.Section(s, e) for s, e in cuts])], rate=inner_rate,
                          ticks=inner_rate * 1000, name='s')
        section = ue.Section(start, start + rng.randint(1, 150), sequence=sub, offset_ticks=rng.randint(0, 40) * 1000)
        seq = ue.Sequence(0, 300, [SHOTS([section])], rate=24)
        shot, = shots_module.discovery_sections(seq, [])
        rows = shots_module._section_rows([shot], 0, 300, [])
        got = {f for _name, lo, hi in rows for f in range(lo, hi)}
        assert got == {f for f in range(0, 300) if shots_module.in_shot_cut(shot, f)}
