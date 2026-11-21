# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""UE process runner rules: timeout, cancel, hang and tree CPU, success marker, log flood (FR-UE-11)."""
import json

import pytest

from sethlans_worker_agent.unreal_executor import log_validation
from sethlans_worker_agent.unreal_executor.runner_types import MB, RENDER_SUCCESS, WARMUP_SUCCESS

from ._process_runner_world import World
from ._runner_fakes import FakePs, make_launch

FATAL = log_validation.fatal_rules()


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def test_timeout_terminates_the_job(world):
    outcome = world.run(world.spec(timeout_s=10))
    assert outcome.reason == 'timeout' and 'terminate' in world.api.calls and outcome.survivors == ()


@pytest.mark.parametrize('cause', ['canceled', 'reassigned', 'shutdown'])
def test_cancel_causes(world, cause):
    world.steps[2] = lambda: world.cause.append(cause)
    world.cause = []
    outcome = world.run(world.spec(cancel=lambda: world.cause[0] if world.cause else None))
    assert outcome.reason == cause and not world.root.alive


def test_shutdown_event(world):
    spec = world.spec()
    world.steps[2] = spec.shutdown_event.set
    assert world.run(spec).reason == 'shutdown'


def test_fatal_line_terminates_at_once(world):
    world.steps[2] = lambda: world.log('[x]LogD3D12RHI: Error: DXGI_ERROR_DEVICE_REMOVED')
    outcome = world.run(world.spec(fatal_rules=FATAL))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'gpu_device_lost'
    assert outcome.rule_counts['gpu_device_lost'] == 1


def test_hang_needs_silence_and_idle_cpu(world):
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000))
    assert outcome.reason == 'hang' and 'CPU-s' in outcome.detail


def test_busy_cpu_is_not_a_hang_even_when_a_worker_exits(tmp_path):
    scw = FakePs(1001, exe='C:/UE/Engine/Binaries/Win64/ShaderCompileWorker.exe')
    world = World(tmp_path, kids=[scw])

    def burn(tick):
        scw.cpu += 10.0 if scw.alive else 0.0
        if tick == 40:
            scw.alive = False  # an exited SCW must not lower the accumulated CPU
        if tick > 40:
            world.root.cpu += 1.0
        if tick == 150:
            world.root.alive = False
    world.scenario = burn
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000))
    assert outcome.reason == 'exited'


def test_zenserver_cpu_is_excluded_by_exact_image(tmp_path):
    zen_image = 'C:/UE/Engine/Binaries/Win64/zenserver.exe'
    zen = FakePs(1002, exe=zen_image)
    world = World(tmp_path, kids=[zen])

    def tick(n):
        zen.cpu += 50.0
    world.scenario = tick
    outcome = world.run(world.spec(hang_s=120, timeout_s=10_000, excluded_images=frozenset({zen_image.lower()})))
    assert outcome.reason == 'hang'


def test_a_lookalike_zenserver_image_is_counted(tmp_path):
    fake_zen = FakePs(1002, exe='D:/other/zenserver_x.exe')
    world = World(tmp_path, kids=[fake_zen])

    def tick(n):
        fake_zen.cpu += 50.0
    world.scenario = tick
    spec = world.spec(hang_s=120, timeout_s=400,
                      excluded_images=frozenset({'c:/ue/engine/binaries/win64/zenserver.exe'}))
    assert world.run(spec).reason == 'timeout'
    assert not fake_zen.alive  # terminated with the job


@pytest.mark.parametrize('phase, marker, kwargs', [
    ('warmup', '[Warm] done, quitting', {'success_marker': WARMUP_SUCCESS}),
    ('render', 'LogWindows: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)',
     {'success_marker': RENDER_SUCCESS}),
])
def test_quit_grace_after_the_success_marker(world, tmp_path, phase, marker, kwargs):
    world.steps[2] = lambda: world.log(marker)
    spec = world.spec(launch=make_launch(tmp_path, phase=phase), quit_grace_s=60, **kwargs)
    outcome = world.run(spec)
    assert outcome.reason == 'exited_after_grace' and outcome.quit_forced and outcome.success_marker_seen
    assert 58 <= outcome.wall_s <= 66
    assert outcome.phase == phase


def test_prep_success_check_counts_as_the_marker(world, tmp_path):
    result = tmp_path / 'prep_result.json'
    world.steps[3] = lambda: result.write_text(json.dumps({'ok': True}))

    def check():
        try:
            return json.loads(result.read_text()).get('ok') is True
        except (OSError, ValueError):
            return False
    outcome = world.run(world.spec(launch=make_launch(tmp_path, phase='prep'), success_check=check))
    assert outcome.reason == 'exited_after_grace' and outcome.success_marker_seen


def test_no_marker_means_hang_and_timeout_rules_apply(world):
    outcome = world.run(world.spec(success_marker=WARMUP_SUCCESS, timeout_s=300, hang_s=900))
    assert outcome.reason == 'timeout' and not outcome.success_marker_seen


def _grow(path, megabytes):
    with open(path, 'ab') as handle:
        handle.write(b'x' * int(megabytes * MB))


def test_log_over_the_size_cap_is_fatal_flood(world, tmp_path):
    world.steps[2] = lambda: _grow(tmp_path / 'render.1.stdout.txt', 17)
    outcome = world.run(world.spec(log_max_bytes=16 * MB, log_flood_bytes_per_min=10_000 * MB))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'log_flood'
    assert outcome.rule_counts['log_flood'] == 1 and 'log flood' in outcome.detail


def test_fast_growth_within_a_minute_is_a_flood(world, tmp_path):
    for tick in range(2, 8):
        world.steps[tick] = lambda: _grow(tmp_path / 'render.1.log', 3)
    outcome = world.run(world.spec(log_max_bytes=512 * MB, log_flood_bytes_per_min=10 * MB))
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'log_flood' and 'MB/min' in outcome.detail


def test_slow_growth_is_not_a_flood(world, tmp_path):
    for tick in range(2, 60, 30):
        world.steps[tick] = lambda: _grow(tmp_path / 'render.1.log', 6)
    world.steps[70] = lambda: setattr(world.root, 'alive', False)
    assert world.run(world.spec(log_flood_bytes_per_min=10 * MB)).reason == 'exited'


def test_flood_after_the_marker_is_a_forced_quit(world, tmp_path):
    world.steps[2] = lambda: world.log('[Warm] done, quitting')
    world.steps[4] = lambda: _grow(tmp_path / 'render.1.log', 20)
    outcome = world.run(world.spec(success_marker=WARMUP_SUCCESS, log_max_bytes=16 * MB))
    assert outcome.reason == 'exited_after_grace' and outcome.quit_forced
    assert outcome.rule_counts['log_flood'] == 1 and outcome.fatal_rule == ''
