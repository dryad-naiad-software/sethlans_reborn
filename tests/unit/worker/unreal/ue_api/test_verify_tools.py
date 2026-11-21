# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE-side verifier (``tools/ue_api/verify_in_unreal.py``) runs end to end against the stub ``unreal`` module
(the real run is by hand on a UE 5.8.4 machine), and ``names_file`` turns its report into evidence: a name UE
lacks is ``MISSING`` and counts as unverified (issue #283).
"""
import json
import runpy
import sys
from pathlib import Path

from tests.unit.worker.unreal import _ue_stub as ue
from tools.ue_api import extract_names, names_file

VERIFIER = Path(extract_names.ROOT) / 'tools' / 'ue_api' / 'verify_in_unreal.py'
SEQ, GRAPH, LEGACY = '/Game/Seq', '/Game/Graph', '/Game/Legacy'


class TopLevelAssetPath:
    """Like UE's: built empty it is a script error (and the commandlet exits 1)."""

    def __init__(self, package='', asset=''):
        if not package.startswith('/'):
            raise RuntimeError('Short path "" not valid for MakeTopLevelAssetPath')


def run_verifier(tmp_path, monkeypatch, names_path=extract_names.DATA_FILE, extra=None):
    """The verifier on the stub; UE's section classes and struct base let it scope its probes as on UE."""
    audio = ue.MovieSceneAudioTrack([ue.AudioSection(0, 10, ue.Sound('/Game/A/S.S'))])
    shot = ue.MovieSceneCinematicShotTrack([ue.shot(0, 100, [(0, 100)])])
    world = {'assets': {SEQ: ue.Sequence(0, 100, [ue.MovieSceneCameraCutTrack([ue.Section(0, 100)]), audio, shot]),
                        GRAPH: ue.graph(), LEGACY: ue.MoviePipelinePrimaryConfig([ue.MoviePipelineOutputSetting()])}}
    module = ue.make_unreal(world)
    module.MovieSceneAudioSection, module.StructBase = ue.AudioSection, ue.Struct
    module.MovieSceneSubSection = type('ShotSection', (ue.Section,), {})  # only shot sections hold parameters
    world['assets'][SEQ].tracks[2].sections[0].__class__ = module.MovieSceneSubSection
    for name, value in (extra or {}).items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, 'unreal', module)
    params = {'names_path': str(names_path), 'output_path': str(tmp_path / 'report.json'),
              'sequence': SEQ, 'presets': [GRAPH, LEGACY]}
    (tmp_path / 'params.json').write_text(json.dumps(params), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_API_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(str(VERIFIER), run_name='sethlans_api_check')
    except RuntimeError as exc:
        error = exc
    return json.loads((tmp_path / 'report.json').read_text(encoding='utf-8')), error


def small_names(tmp_path, unreal_names):
    path = tmp_path / 'names.json'
    names_file.update(path, {'unreal': unreal_names, 'static': ['MovieGraphLibrary.named_resolution_from_size'],
                             'properties': ['output_resolution', 'time_scale'], 'members': ['get_tracks']}, {})
    return path


def test_the_verifier_reports_every_listed_name_with_no_expected_errors(tmp_path, monkeypatch):
    report, _error = run_verifier(tmp_path, monkeypatch)
    data = names_file.load(extract_names.DATA_FILE)
    for category in names_file.CATEGORIES:
        assert set(report[category]) == names_file.names(data, category), category
    assert report['unreal']['MovieGraphLibrary']['ok'] is True
    assert report['static']['MovieGraphLibrary.named_resolution_from_size']['ok'] is True
    assert 'MovieGraphGlobalOutputSettingNode' in report['properties']['output_resolution']['found_on']
    assert 'Sequence' in report['members']['get_tracks']['found_on']  # a live object reached from the sequence
    assert 'MoviePipelineOutputSetting' in report['pool']
    assert report['errors'] == []  # parameters probed only on shot sections, never on audio or camera-cut ones


def test_every_name_found_exits_cleanly_and_a_missing_one_raises(tmp_path, monkeypatch):
    report, error = run_verifier(tmp_path, monkeypatch, small_names(tmp_path, ['MovieGraphLibrary']))
    assert error is None and report['errors'] == []
    assert 'MovieSceneSectionParameters' not in report['pool'] and report['properties']['time_scale']['found_on']
    report, error = run_verifier(tmp_path, monkeypatch, small_names(tmp_path, ['MovieGraphBlueprintLibrary']))
    assert report['unreal']['MovieGraphBlueprintLibrary']['ok'] is False  # the report is written first
    assert error is not None and 'unreal:MovieGraphBlueprintLibrary' in str(error)


def test_a_property_or_member_nothing_has_raises_after_the_report(tmp_path, monkeypatch):
    """The exit status comes from the whole report: a static call or a member no probed object has counts too."""
    path = tmp_path / 'names.json'
    names_file.update(path, {'unreal': ['MovieGraphLibrary'], 'members': ['get_tracks', 'get_nothing'],
                             'static': ['MovieGraphLibrary.named_resolution_from_size', 'MovieGraphLibrary.no_call']}, {})
    report, error = run_verifier(tmp_path, monkeypatch, path)
    assert report['static']['MovieGraphLibrary.no_call']['ok'] is False and report['members']['get_nothing']['found_on'] == []
    assert report['static']['MovieGraphLibrary.named_resolution_from_size']['ok']
    assert report['members']['get_tracks']['found_on']
    assert error is not None and str(error) == ("2 listed Unreal names are missing: static:MovieGraphLibrary.no_call, "
                                                "members:get_nothing")


def test_a_struct_that_rejects_empty_values_is_built_with_valid_ones(tmp_path, monkeypatch):
    names = small_names(tmp_path, ['MovieGraphLibrary', 'TopLevelAssetPath'])
    report, error = run_verifier(tmp_path, monkeypatch, names, extra={'TopLevelAssetPath': TopLevelAssetPath})
    assert error is None and report['errors'] == [] and report['unreal']['TopLevelAssetPath']['ok'] is True


def test_a_ue_report_becomes_evidence_and_missing_names_fail(tmp_path):
    path = tmp_path / 'names.json'
    names_file.update(path, {'unreal': ['MovieGraphLibrary', 'Gone'], 'members': ['get_tracks']}, {})
    unreal = {'MovieGraphLibrary': {'ok': True, 'type': 'MovieGraphLibrary'}, 'Gone': {'ok': False, 'type': 'NoneType'}}
    report = {'build': '5.8.4-58902052', 'unreal': unreal, 'members': {'get_tracks': {'found_on': ['LevelSequence']}}}
    left = names_file.merge_ue_report(path, report)
    data = names_file.load(path)
    assert data['names']['unreal']['MovieGraphLibrary']['unreal'] == 'UE 5.8.4-58902052: MovieGraphLibrary'
    assert data['names']['members']['get_tracks']['unreal'] == 'UE 5.8.4-58902052: LevelSequence'
    assert left == ['unreal:Gone']  # a name UE lacks is never verified, whatever the source said


def test_update_keeps_evidence_and_drops_names_nothing_uses(tmp_path):
    path = tmp_path / 'names.json'
    names_file.update(path, {'unreal': ['A', 'B']}, {'unreal': ['B']})
    names_file.merge(path, 'source', {'unreal': {'A': 'UA A.h:1', 'B': 'UB B.h:2'}})
    assert names_file.update(path, {'unreal': ['A']}, {}) == ['unreal:A']  # source evidence alone does not verify
    data = names_file.load(path)
    assert data['names']['unreal'] == {'A': {'source': 'UA A.h:1', 'unreal': '', 'used_by': ['scripts']}}


def test_only_a_real_unreal_run_verifies_a_name(tmp_path):
    """#283 gatekeeper: source evidence is a first check; the ``unreal`` field from a real UE run is required."""
    path = tmp_path / 'names.json'
    names_file.update(path, {'unreal': ['A', 'B', 'C']}, {})
    names_file.merge(path, 'source', {'unreal': {'A': 'UA A.h:1', 'B': 'UB B.h:2', 'C': 'UC C.h:3'}})
    names_file.merge(path, 'unreal', {'unreal': {'B': 'UE 5.8.4: B', 'C': 'MISSING'}})
    assert names_file.unverified(names_file.load(path)) == ['unreal:A', 'unreal:C']
