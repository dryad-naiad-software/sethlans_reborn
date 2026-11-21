# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unreal slots: paths, ports, Zen flags, launch checks, farm zenserver identity, port range and marker
(render per card D5, D6, FR-PC-SLOT-1..4, FR-PC-SLOT-7; AC-4, AC-5)."""
import logging

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import command, user_state, warmup, zen_log, zen_private
from sethlans_worker_agent.unreal_executor import slot as slots
from sethlans_worker_agent.unreal_executor.command import CommandError
from tests.unit.worker.unreal.test_command import EDITOR, JOB, MAP, MERGED, QUEUE, UPROJECT
from tests.unit.worker.unreal.test_zen_private import FakeProc, owners  # noqa: F401 - fixture

CACHE = r'D:\uecache'
LOG = JOB + r'\logs\render.1.log'


class TestSlotPaths:

    @pytest.mark.parametrize('n, profile, zen, port', [
        (0, r'D:\uecache\ueprofile', r'D:\uecache\zen', 8668),
        (1, r'D:\uecache\ueprofile-s1', r'D:\uecache\zen-s1', 8669),
        (15, r'D:\uecache\ueprofile-s15', r'D:\uecache\zen-s15', 8683),
    ])
    def test_paths_and_port(self, n, profile, zen, port):
        slot = slots.slot_for(CACHE, 8668, n)
        assert (slot.n, slot.cache_dir, slot.profile, slot.zen_port) == (n, CACHE, profile, port)
        assert (slot.zen_data, slot.zen_fs) == (zen + r'\data', zen + r'\fs')

    def test_slot_0_is_todays_paths(self):
        slot = slots.slot_for(CACHE, 8668, 0)
        assert slot.profile == user_state.profile_dir(CACHE)
        assert (slot.zen_data, slot.zen_fs) == (zen_private.zen_data_dir(CACHE), zen_private.zen_fs_dir(CACHE))

    def test_two_jobs_of_one_archive_get_their_own_work_copy(self, tmp_path):
        """AC-4: the work copy's name ends with the slot (``ArchiveRequest.slot``, FR-PC-SLOT-6)."""
        from sethlans_worker_agent.project_archive.layout import CacheLayout
        layout = CacheLayout(tmp_path)
        names = [layout.work_base_name('a' * 64, 'b' * 64, n) for n in (0, 1)]
        assert names[0].endswith('-s0') and names[1].endswith('-s1') and names[0][:-1] == names[1][:-1]

    def test_all_slots_are_distinct(self):
        every = slots.all_slots(CACHE, 8668)
        assert [slot.n for slot in every] == list(range(16))
        for field in ('profile', 'zen_data', 'zen_fs', 'zen_port'):
            assert len({getattr(slot, field) for slot in every}) == 16

    @pytest.mark.parametrize('n', [-1, 16, True, 1.0, '1'])
    def test_a_slot_outside_the_range_is_refused(self, n):
        for call in (lambda: slots.slot_for(CACHE, 8668, n), lambda: user_state.profile_dir(CACHE, n),
                     lambda: zen_private.zen_data_dir(CACHE, n), lambda: warmup.marker_path('x', '', 'b', n)):
            with pytest.raises(ValueError):
                call()

    def test_a_slot_port_on_the_artists_port_is_refused(self):
        with pytest.raises(ValueError):
            slots.slot_for(CACHE, 8543, 15)  # 8543 + 15 = 8558


class TestSlotPool:

    def test_lowest_free_first_and_given_back(self):
        pool = slots.SlotPool()
        assert [pool.take() for _ in range(3)] == [0, 1, 2]
        pool.give(1)
        assert pool.take() == 1 and pool.free() == tuple(range(3, 16))

    def test_empty_pool_gives_none(self):
        pool = slots.SlotPool(count=1)
        assert pool.take() == 0 and pool.take() is None

    @pytest.mark.parametrize('n', [0, 16, -1, True])
    def test_giving_back_a_slot_that_is_not_taken_is_loud(self, n):
        with pytest.raises(ValueError):
            slots.SlotPool().give(n)


class TestZenFlagsAndLaunch:

    def test_slot_flags(self):
        assert zen_private.zen_flags(CACHE, 8669, 1) == (
            r'-ZenDataPath=D:\uecache\zen-s1\data', r'-LocalDataCachePath=D:\uecache\zen-s1\fs',
            '-SharedDataCachePath=None',
            '-ini:Engine:[Zen.AutoLaunch]:DesiredPort=8669,[Zen.AutoLaunch]:RemoteNetworkService=None')

    def test_slot_0_flags_and_launch_are_byte_identical_to_today(self):
        assert zen_private.zen_flags(CACHE, 8668, 0) == zen_private.zen_flags(CACHE, 8668)
        today = command.build_render(command.context_for(EDITOR, CACHE, 8668, graphics_adapter=1), UPROJECT, MAP,
                                     QUEUE, LOG, MERGED)
        ctx = command.context_for_slot(EDITOR, slots.slot_for(CACHE, 8668, 0), graphics_adapter=1)
        launch = command.build_render(ctx, UPROJECT, MAP, QUEUE, LOG, MERGED)
        assert launch.args == today.args and launch.env == today.env
        assert (launch.profile, launch.cwd) == (today.profile, today.cwd)

    def test_slot_1_launch_names_its_profile_zen_folders_and_port(self):
        """AC-4: all seven isolation variables, zen-s1\\data, zen-s1\\fs and zen_port + 1."""
        ctx = command.context_for_slot(EDITOR, slots.slot_for(CACHE, 8668, 1), graphics_adapter=1)
        launch = command.build_render(ctx, UPROJECT, MAP, QUEUE, LOG, MERGED)
        profile = r'D:\uecache\ueprofile-s1'
        assert launch.profile == profile and ctx.slot == 1 and ctx.zen_port == 8669
        for name in user_state.ISOLATION_VARS:
            value = launch.env[name]
            assert value == 'D:' if name == 'HOMEDRIVE' else ('ueprofile-s1' in value), name
        assert launch.env['TEMP'] == launch.env['TMP'] == profile + r'\Temp'
        assert r'-ZenDataPath=D:\uecache\zen-s1\data' in launch.args
        assert r'-LocalDataCachePath=D:\uecache\zen-s1\fs' in launch.args
        assert '-ini:Engine:[Zen.AutoLaunch]:DesiredPort=8669,[Zen.AutoLaunch]:RemoteNetworkService=None' in launch.args
        assert not any(r'\zen\data' in arg or 'DesiredPort=8668' in arg for arg in launch.args)

    @pytest.mark.parametrize('built, checked', [(0, 1), (1, 0), (1, 2)])
    def test_validate_args_refuses_another_slots_command_line(self, built, checked):
        def ctx(n):
            return command.context_for_slot(EDITOR, slots.slot_for(CACHE, 8668, n), graphics_adapter=1)
        args = command.build_render(ctx(built), UPROJECT, MAP, QUEUE, LOG, MERGED).args
        command.validate_args(args, ctx(built), phase='render')
        with pytest.raises(CommandError):
            command.validate_args(args, ctx(checked), phase='render')


class TestFarmZenserver:

    @pytest.mark.parametrize('n', range(16))
    def test_every_slots_server_is_a_farm_zenserver(self, owners, n):  # noqa: F811
        data = zen_private.zen_data_dir(CACHE, n)
        assert zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', '--data-dir', data]), CACHE)
        assert zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', f'--data-dir="{data.upper()}"']), CACHE)

    @pytest.mark.parametrize('data_dir', [
        r'D:\uecache\zen-s16\data', r'D:\uecache\zen-s01\data', r'D:\uecache\zen-s1\data\x',
        r'D:\uecache\zen-s1\fs', r'D:\uecache\zen-s0\data', r'D:\uecache2\zen-s1\data', r'D:\uecache\ueprofile-s1',
        r'C:\Users\artist\AppData\Local\UnrealEngine\Common\Zen\Data',
    ])
    def test_a_foreign_data_dir_is_not(self, owners, data_dir):  # noqa: F811
        assert not zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', '--data-dir', data_dir]), CACHE)

    def test_another_owner_of_a_slot_server_is_not(self, owners):  # noqa: F811
        proc = FakeProc(['zenserver.exe', '--data-dir', zen_private.zen_data_dir(CACHE, 3)], pid=7)
        owners[7] = 'S-1-5-21-1-2-3-1002'
        assert not zen_private.is_farm_zenserver(proc, CACHE)


class TestZenLog:

    def _lines(self, n, port):
        data = zen_private.zen_data_dir(CACHE, n)
        return [f'LogZenServiceInstance: Display: Found command line override ZenDataPath={data}',
                f"LogZenServiceInstance: Display: Launching executable 'z.exe', working dir 'w', data dir '{data}', "
                f"args '--port {port} --data-dir \"{data}\"'"]

    def test_a_slot_log_passes_for_its_slot(self):
        assert zen_log.verify_lines(self._lines(1, 8669), CACHE, 8669, slot=1).ok

    @pytest.mark.parametrize('n, port', [(0, 8669), (2, 8669), (1, 8668)])
    def test_another_slots_folder_or_port_fails(self, n, port):
        assert not zen_log.verify_lines(self._lines(n, port), CACHE, 8669, slot=1).ok


class TestPortRange:

    @staticmethod
    def _load(port):
        return config_unreal.load(lambda section, key, default: port if key == 'zen_port' else default)['ZEN_PORT']

    @pytest.mark.parametrize('port', ['1024', '8542', '8559', '9100', '65519'])
    def test_accepted(self, port):
        assert self._load(port) == int(port)

    @pytest.mark.parametrize('port', ['8543', '8550', '8558', '65520', '65535', '1023'])
    def test_refused_with_a_warning(self, caplog, port):
        with caplog.at_level(logging.WARNING):
            assert self._load(port) == 8668
        assert any('zen_port' in record.getMessage() for record in caplog.records)


class TestMarker:

    @pytest.mark.parametrize('n, name', [(0, '5.8.4-1.json'), (1, '5.8.4-1-s1.json'), (15, '5.8.4-1-s15.json')])
    def test_path(self, tmp_path, n, name):
        path = warmup.marker_path(tmp_path, 'c' * 64, '5.8.4-1', n)
        assert path.relative_to(tmp_path).parts == ('warmup', 'c' * 16, name)

    def test_slot_0_warm_does_not_mark_slot_1_warm(self, tmp_path):
        """AC-5 (first half)."""
        warmup.record_success(warmup.marker_path(tmp_path, '', 'b', 0), '/m', '/s', 'sha')
        assert not warmup.needs_warmup(warmup.marker_path(tmp_path, '', 'b', 0), '/m', '/s', 'sha')
        assert warmup.needs_warmup(warmup.marker_path(tmp_path, '', 'b', 1), '/m', '/s', 'sha')
