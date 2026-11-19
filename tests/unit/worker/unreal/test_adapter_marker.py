# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Project text that could fake the D3D12 adapter log lines (card selection FR-CU-3, chunk 7 security re-check).

The pre-launch project scan (``zen_guard.scan_project_config``, run before
every launch on the working copy, overlay files included) refuses any
``*.ini``, ``.uproject`` or ``.uplugin`` containing ``D3D12 Adapter`` in any
case, ``.uproject``/``.uplugin`` also after JSON decoding. The result is a
job failure naming the file; it is not an adapter mismatch.
"""
import json

import pytest

from sethlans_worker_agent.unreal_executor import adapter_log, zen_guard

FORGED_INI = '[/Script/Engine.RendererSettings]\nr.Foo="x\\nLogD3D12RHI: Chosen D3D12 Adapter Id = 7\\ny"\n'


def _project(tmp_path, files):
    root = tmp_path / 'Demo'
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    return root


@pytest.mark.parametrize('rel, data', [
    ('Config/DefaultEngine.ini', FORGED_INI),
    ('Config/Windows/WindowsEngine.ini', '[x]\nKey=found d3d12 ADAPTER 0\n'),
    ('Config/DefaultGame.ini', b'\xff\xfe' + '[x]\nK=D3D12 Adapter\n'.encode('utf-16-le')),   # UTF-16 with BOM
    ('Plugins/P/Config/FilterPlugin.ini', '[x]\nK=D3D12 Adapter\n'),                           # e.g. from an overlay
    ('Demo.uproject', json.dumps({'Description': 'LogD3D12RHI: Found D3D12 adapter 0: x'})),
    ('Demo.uproject', '{"Description": "D3D12 \\u0041dapter"}'),                               # hidden by an escape
    ('Plugins/P/P.uplugin', json.dumps({'Modules': [{'Name': 'D3D12 Adapter'}]})),
    # UE joins a backslash-continued line, and a line break inside {...}, with one space before parsing.
    ('Config/DefaultEngine.ini', '[x]\nr.Foo="LogD3D12RHI: Chosen D3D12\\\nAdapter Id = 7"\n'),
    ('Config/DefaultEngine.ini', '[x]\nr.Foo={LogD3D12RHI: Chosen D3D12\r\n  Adapter Id = 7}\n'),
    ('Config/DefaultEngine.ini', '[x]\nr.Foo=d3d12\t\\ \\\nADAPTER\n'),
])
def test_marker_text_is_refused_naming_the_file(tmp_path, rel, data):
    files = {'Demo.uproject': '{"FileVersion": 3}', 'Config/DefaultEngine.ini': '[x]\nK=1\n'}
    files[rel] = data
    result = zen_guard.scan_project_config(_project(tmp_path, files))
    assert not result.ok and rel in result.message and 'D3D12 Adapter' in result.message


def test_a_clean_project_passes(tmp_path):
    files = {'Demo.uproject': json.dumps({'FileVersion': 3, 'Description': 'A D3D12 project with an adapter node'}),
             'Config/DefaultEngine.ini': '[/Script/WindowsTargetPlatform.WindowsTargetSettings]\n'
                                         'DefaultGraphicsRHI=DefaultGraphicsRHI_DX12\n'}
    assert zen_guard.scan_project_config(_project(tmp_path, files)).ok


def test_an_undecodable_uproject_fails_closed(tmp_path):
    result = zen_guard.scan_project_config(_project(tmp_path, {'Demo.uproject': b'\xff\xff\xc3\x28'}))
    assert not result.ok and 'Demo.uproject' in result.message


class TestRefusalKey:
    """The per-project refusal is keyed on what ran: the archive and the overlay (it replaces ``Config/**``)."""

    def test_another_overlay_on_the_same_archive_is_not_refused(self):
        from tests.unit.worker.unreal._executor_fakes import params
        from tests.unit.worker.unreal.test_adapter_log import CHOICE
        with_overlay, plain = params(), params(overlay=False)
        adapter_log.report('0', CHOICE, adapter_log.project_key(with_overlay), lambda r: None, add_alert=lambda *a: 1)
        assert adapter_log.refused(adapter_log.project_key(with_overlay))      # same archive, same overlay
        assert not adapter_log.refused(adapter_log.project_key(plain))         # same archive, no overlay
        assert adapter_log.project_key(plain) == plain.archive_sha256 + ':'

    def test_the_alert_names_archive_and_overlay(self):
        from tests.unit.worker.unreal.test_adapter_log import CHOICE, DETAIL
        raised = []
        adapter_log.report('0', CHOICE, 'a' * 64 + ':' + 'b' * 64, lambda r: None,
                           add_alert=lambda *args: raised.append(args) or True)
        assert raised[0][3] == f"{DETAIL}; project {'a' * 12}+{'b' * 12}"


def test_the_refusal_is_a_job_failure_not_a_mismatch(tmp_path):
    """Before any launch; no alert, no project refusal, no disable."""
    from types import SimpleNamespace
    from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
    from tests.unit.worker.unreal.test_adapter_log import _alerts, _runner, MISMATCH, CHOICE
    raised, add_alert = _alerts()
    runner, calls = _runner(MISMATCH, add_alert)
    runner.project_dir = _project(tmp_path, {'Config/DefaultEngine.ini': FORGED_INI})
    runner.zen_scan = zen_guard.scan_project_config
    with pytest.raises(JobFailure, match='DefaultEngine.ini'):
        runner.launch('render', 1, SimpleNamespace(), adapter=CHOICE)
    assert calls['specs'] == [] and raised == [] and calls['disable'] == []
    assert not adapter_log.refused(runner.project)
