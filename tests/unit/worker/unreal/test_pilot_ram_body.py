# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pilot-result body's RAM fields (per-card FR-PC-RAM-3): always sent, defaults until the runner measures."""
from sethlans_worker_agent.gpu_memory.types import GpuMem
from sethlans_worker_agent.unreal_executor import pilot

METRICS = pilot.PilotMetrics(peak_mb=7000, peak_source='pdh', card_delta_peak_mb=6000, csv_local_used_peak_mb=None,
                             baseline_used_mb=1000, baseline_drift_mb=0, peak_phase='render', gpu_index=0,
                             max_sample_gap_s=1.0)
GPU = GpuMem(0, 'Test Card A', 16384, 1000, None, None, 'pci:0000:0a:00.0')
RAM = {'prep': {'ws_mb': 3000, 'commit_mb': 5000},
       'warmup': {'ws_mb': 6000, 'commit_mb': 20000, 'cold': True},
       'render': {'ws_mb': 9000, 'commit_mb': 28000}}


def body(**kwargs):
    return pilot.report_body(METRICS, GPU, prep_s=1, warmup_s=1, render_s=10, frames=5,
                             engine_build='5.8.4-58902052', overrides_sha256='c' * 64, summary='s',
                             card_kind='10de:2b85:00a1', driver_version=None, **kwargs)


def test_the_fields_are_always_sent_with_unknown_defaults():
    sent = body()
    assert sent['ram'] is None
    assert sent['ram_process_commit_peak_mb'] is None
    assert sent['shared_machine'] is False


def test_measured_figures_are_sent_as_given():
    sent = body(ram=RAM, ram_process_commit_peak_mb=27000, shared_machine=True)
    assert sent['ram'] == RAM
    assert sent['ram_process_commit_peak_mb'] == 27000
    assert sent['shared_machine'] is True


def test_a_pilot_without_a_warm_up_sends_a_null_warmup():
    ram = dict(RAM, warmup=None)
    assert body(ram=ram)['ram'] == {'prep': RAM['prep'], 'warmup': None, 'render': RAM['render']}


def test_the_ram_keys_are_the_managers_schema():
    """Exactly the keys of ``manager/workers/serializers/pilot_ram.py`` and the three top-level fields."""
    sent = body(ram=RAM, ram_process_commit_peak_mb=27000)
    assert {'ram', 'ram_process_commit_peak_mb', 'shared_machine'} <= set(sent)
    assert set(sent['ram']) == {'prep', 'warmup', 'render'}
    assert set(sent['ram']['prep']) == set(sent['ram']['render']) == {'ws_mb', 'commit_mb'}
    assert set(sent['ram']['warmup']) == {'ws_mb', 'commit_mb', 'cold'}
