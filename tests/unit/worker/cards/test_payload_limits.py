# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-CR-1 limits of the ``cards`` object: read-only, probe cadence, caps, size.

- Read-only (S1): building the report with a ticked card missing raises no
  alert, stops no job, requests no rebuild and leaves ``config.json`` as it
  was (a real file under ``tmp_path``).
- Cadence: the heartbeat reuses the card pages' probe cache for one
  heartbeat interval, and runs no probe while an Unreal render holds the
  machine (fake clock on ``cards_cache.time``; ``cards_view.probe_now`` is
  faked, so no real probe runs and Blender is never started).
- Caps: at most 16 entries per list (ticked cards kept first), strings in
  printable ASCII within the manager's caps.
- Size: a worst-case report inside a whole heartbeat at the manager's caps
  stays well under the manager's 128 KiB body cap.
"""
import json
import types

import pytest

from sethlans_worker_agent import blender_stop, config, config_compute, config_store, unreal_bridge
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice, in_use, payload, store
from sethlans_worker_agent.web_ui import cards_cache
from tests.unit.worker._card_choice import KEY_RTX_A, inv, rtx_a, xt_03, xt_23
from tests.unit.worker._card_pages import fake_probes

REAL_PROBE_FOR_REPORT = payload._probe_for_report  # taken before the autouse fixture replaces it
REAL_READ_RAW = config_compute._read_raw
BODY_CAP = 128 * 1024
NASTY = '"\\é\U0001f600<>`\x07'  # characters that JSON escapes or the manager replaces


@pytest.fixture
def real_seam(monkeypatch):
    monkeypatch.setattr(payload, '_probe_for_report', REAL_PROBE_FOR_REPORT)
    monkeypatch.setattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 30)
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(cards_cache, 'time', types.SimpleNamespace(monotonic=lambda: clock.now))
    return clock


class TestReadOnly:

    @pytest.fixture
    def config_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(tmp_path))
        monkeypatch.setattr(config_store, 'load_system_config', lambda: {})
        monkeypatch.setattr(config_compute, '_read_raw', REAL_READ_RAW)
        compute = {'method': 'gpu', 'gpu_mode': 'split', 'revision': 3, 'alerts': [],
                   'cards': [in_use.record_of(rtx_a()), in_use.record_of(xt_23())]}
        path = tmp_path / 'config.json'
        path.write_text(json.dumps({'compute': compute}), encoding='utf-8')
        return path

    def test_report_with_a_missing_card_changes_nothing(self, monkeypatch, config_file, real_seam):
        calls = []
        for module, name in ((store, 'add_alert'), (store, 'remove_alerts'), (blender_stop, 'request_card_stop'),
                             (choice, '_request_rebuild'), (rebuild, 'request_rebuild'), (config_store, 'set_many'),
                             (config_store, 'save')):
            monkeypatch.setattr(module, name, lambda *a, _n=f'{module.__name__}.{name}', **k: calls.append(_n))
        fake_probes(monkeypatch, inv(xt_03(), xt_23()))
        before = config_file.read_bytes()

        for step in range(5):
            real_seam.now = step * 40.0  # a new probe each time
            report = payload.build()
            assert {e['key']: e['state'] for e in report['cards']}[KEY_RTX_A] == 'lost'

        assert calls == []
        assert rebuild.state() == (0, False)
        assert config_file.read_bytes() == before
        assert sorted(p.name for p in config_file.parent.iterdir()) == ['config.json']


class TestCadence:

    def test_one_probe_per_heartbeat_interval(self, monkeypatch, real_seam):
        probes = fake_probes(monkeypatch)
        for now in (0.0, 5.0, 10.0, 29.9, 30.0):
            real_seam.now = now
            payload.build()
        assert len(probes) == 1
        real_seam.now = 30.1
        payload.build()
        assert len(probes) == 2

    def test_a_page_probe_is_reused(self, monkeypatch, real_seam):
        probes = fake_probes(monkeypatch)
        cards_cache.cached_probe()
        real_seam.now = 20.0
        payload.build()
        assert len(probes) == 1

    def test_no_probe_while_an_unreal_render_holds_the_machine(self, monkeypatch, real_seam):
        probes = fake_probes(monkeypatch)
        payload.build()
        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: False)
        real_seam.now = 10_000.0
        assert payload.build()['schema'] == 1
        assert len(probes) == 1

    def test_unreal_hold_with_nothing_cached_leaves_cards_out(self, monkeypatch, real_seam, caplog):
        probes = fake_probes(monkeypatch)
        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: False)
        heartbeat = {'status': 'RENDERING'}

        assert payload.build() is None
        payload.add_to(heartbeat)

        assert probes == [] and heartbeat == {'status': 'RENDERING'}
        assert [r for r in caplog.records if r.name == payload.logger.name and r.levelno > 10] == []
        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: True)
        assert payload.build()['schema'] == 1  # the hold is over: a real probe runs
        assert len(probes) == 1

    def test_heartbeat_interval_below_the_page_cache_age_uses_the_page_age(self, monkeypatch, real_seam):
        monkeypatch.setattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 1)
        probes = fake_probes(monkeypatch)
        for now in (0.0, 4.9):
            real_seam.now = now
            payload.build()
        assert len(probes) == 1


def _answer(entries: int, long: str) -> dict:
    """A card GET answer with ``entries`` of everything and every text at ``long``."""
    def key(index, base=0):
        return f'pci:0000:{base + index:02x}:00.0'
    return {
        'method': 'gpu', 'gpu_mode': 'split', 'revision': 2 ** 31 - 1, 'rebuild_waiting': True, 'problem': long,
        'cards': [{'key': key(i), 'name': long, 'vendor_id': 0xFFFF, 'device_id': 0xFFFF, 'backend': 'OPTIX',
                   'note': long, 'ticked': i % 2 == 0, 'state': 'ok' if i % 2 == 0 else 'unticked',
                   'screen': True, 'primary_screen': True} for i in range(entries)],
        'missing': [{'key': key(i, 0x80), 'name': long, 'vendor_id': 0xFFFF, 'device_id': 0xFFFF, 'state': 'lost'}
                    for i in range(entries)],
        'alerts': [{'id': f'{i:032x}', 'kind': 'card_lost_blender', 'key': key(i), 'name': long, 'detail': long,
                    'raised_at': '2026-11-19T08:00:00+00:00'} for i in range(entries)],
        'notices': [{'kind': 'card_gone', 'key': key(i, 0x40), 'name': long} for i in range(entries)],
        'warnings': [{'code': 'ticked_drives_screen', 'key': key(i), 'text': long} for i in range(entries)],
    }


def _section():
    return config_compute.parse({}, {})


class TestCaps:

    def test_every_list_is_capped_at_16_and_ticked_cards_are_kept(self):
        report = payload.shape(_answer(40, 'x'), _section(), os_probe_failed=True)

        for name in ('cards', 'alerts', 'notices', 'warnings'):
            assert len(report[name]) == 16, name
        assert all(entry['ticked'] for entry in report['cards'])
        assert report['warnings'][0]['code'] == 'os_probe_failed'
        assert report['alerts'][-1]['id'] == f'{39:032x}'  # the newest are kept

    def test_ticked_first_then_unticked_in_address_order(self):
        answer = _answer(20, 'x')  # 10 ticked and 10 unticked inventory cards
        answer['missing'] = answer['missing'][:2]  # and 2 ticked cards that are gone
        report = payload.shape(answer, _section(), os_probe_failed=False)
        keys = [entry['key'] for entry in report['cards']]
        assert len(keys) == 16 and sum(entry['ticked'] for entry in report['cards']) == 12
        assert keys == sorted(keys)
        assert [entry['key'] for entry in report['cards'] if not entry['ticked']] == [
            f'pci:0000:{i:02x}:00.0' for i in (1, 3, 5, 7)]

    def test_strings_are_printable_ascii_within_the_manager_caps(self):
        report = payload.shape(_answer(16, NASTY * 100), _section(), os_probe_failed=False)

        assert len(report['problem']) == 200
        for entry in report['cards'] + report['notices']:
            assert len(entry['name']) == 128
        for entry in report['cards']:
            assert len(entry['note']) <= 200
        for entry in report['alerts']:
            assert len(entry['name']) == 128 and len(entry['detail']) == 200
        assert all(len(w['text']) == 200 for w in report['warnings'])
        assert all(' ' <= ch <= '~' for ch in json.dumps(report, ensure_ascii=False))


def _whole_heartbeat(report: dict) -> dict:
    """A heartbeat with every other field at the manager's caps (16 GPUs, 16 installs, 256 job ids)."""
    long = 'x' * 1024
    gpus = [{'id': 'CUDA_' + 'N' * 120 + f'_0000:{i:02x}:00_OptiX', 'name': 'N' * 128, 'type': 'OPTIX'}
            for i in range(16)]
    return {
        'hostname': 'h' * 255, 'ip_address': '255.255.255.255', 'os': 'w' * 255, 'cpu_name': 'c' * 255,
        'cpu_threads': 1024, 'status': 'RENDERING', 'worker_boot_id': 'f' * 32, 'ui_url': 'https://' + 'u' * 255,
        'ui_cert_fingerprint': 'ab' * 32,
        'available_tools': {'blender': [f'5.{i}.{i}' for i in range(32)], 'gpu_devices': [g['id'] for g in gpus],
                            'gpu_devices_details': gpus},
        'schedule': {'enabled': True, 'windows': [{'days': list(range(7)), 'start': '00:00', 'end': '23:59'}] * 8},
        'unreal_capabilities': {'schema': 1, 'enabled': True, 'disabled_reason': 'r' * 200, 'share': long,
                                'share_reachable': True,
                                'installs': [{'version': '5.8.0', 'source': 'launcher', 'path': long}] * 16},
        'gpu_memory': {'source': 'override', 'age_s': 86_400,
                       'gpus': [{'index': i, 'name': 'N' * 128, 'total_mb': 1_048_576, 'used_mb': 1_048_576}
                                for i in range(16)]},
        'active_job_ids': list(range(2 ** 31 - 257, 2 ** 31 - 1)),
        'cards': report,
    }


class TestSize:

    def test_worst_case_report_in_a_whole_heartbeat_is_well_under_128_kib(self):
        plain = payload.shape(_answer(40, 'x' * 1000), _section(), os_probe_failed=True)
        escaped = payload.shape(_answer(40, NASTY * 200), _section(), os_probe_failed=True)

        plain_size = len(json.dumps(plain).encode())
        escaped_size = len(json.dumps(escaped).encode())
        whole = len(json.dumps(_whole_heartbeat(escaped)).encode())

        # Measured 2026-11-19: about 24 KB plain, 28 KB escaped, 63 KB for the whole heartbeat.
        assert plain_size < 26 * 1024          # every text at its cap, no escapes
        assert escaped_size < 32 * 1024        # every text at its cap, some of it JSON-escaped
        assert whole < 80 * 1024               # well under the manager's body cap
        assert whole < BODY_CAP
