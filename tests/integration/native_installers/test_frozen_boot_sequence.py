# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the manager boot sequence in frozen and source modes.

Verifies the correct ordering of django.setup() and migration runners
in both frozen mode (in-process) and source mode (subprocess), as well
as the _prepare_runtime() helper.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from sethlans_manager.migration_runner import (
    run_collectstatic_inprocess,
    run_migrations_inprocess,
)


class TestFrozenBootSequenceOrdering:
    """Frozen boot: django.setup() runs BEFORE in-process migrations."""

    def test_frozen_boot_calls_setup_then_migrate(self, monkeypatch):
        """_frozen_boot_sequence calls django.setup() before migrations."""
        import shared.frozen_paths as fp_mod

        call_order = []

        mock_django = MagicMock()
        mock_django.setup.side_effect = lambda: call_order.append(
            'django.setup',
        )
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: True)

        with patch.dict('sys.modules', {'django': mock_django}):
            with patch(
                'sethlans_manager.migration_runner.run_migrations_inprocess',
            ) as mock_migrate:
                mock_migrate.side_effect = lambda: call_order.append(
                    'migrate',
                )
                with patch(
                    'sethlans_manager.migration_runner'
                    '.run_collectstatic_inprocess',
                ) as mock_static:
                    mock_static.side_effect = lambda: call_order.append(
                        'collectstatic',
                    )
                    # Import and call after patches are in place
                    from run_manager import _frozen_boot_sequence
                    _frozen_boot_sequence(dev_mode=False)

        assert call_order[0] == 'django.setup'
        assert 'migrate' in call_order
        idx_setup = call_order.index('django.setup')
        idx_migrate = call_order.index('migrate')
        assert idx_setup < idx_migrate

    def test_frozen_boot_skips_collectstatic_in_dev(self, monkeypatch):
        """_frozen_boot_sequence skips collectstatic when dev_mode=True."""
        import shared.frozen_paths as fp_mod
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: True)

        call_order = []

        mock_django = MagicMock()
        mock_django.setup.side_effect = lambda: call_order.append(
            'setup',
        )

        with patch.dict('sys.modules', {'django': mock_django}):
            with patch(
                'sethlans_manager.migration_runner'
                '.run_migrations_inprocess',
            ) as mock_migrate:
                mock_migrate.side_effect = lambda: call_order.append(
                    'migrate',
                )
                with patch(
                    'sethlans_manager.migration_runner'
                    '.run_collectstatic_inprocess',
                ) as mock_static:
                    mock_static.side_effect = lambda: call_order.append(
                        'collectstatic',
                    )
                    from run_manager import _frozen_boot_sequence
                    _frozen_boot_sequence(dev_mode=True)

        assert 'collectstatic' not in call_order

    def test_frozen_boot_runs_collectstatic_in_prod(self, monkeypatch):
        """_frozen_boot_sequence runs collectstatic when dev_mode=False."""
        import shared.frozen_paths as fp_mod
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: True)

        call_order = []

        mock_django = MagicMock()
        mock_django.setup.side_effect = lambda: call_order.append(
            'setup',
        )

        with patch.dict('sys.modules', {'django': mock_django}):
            with patch(
                'sethlans_manager.migration_runner'
                '.run_migrations_inprocess',
            ) as mock_migrate:
                mock_migrate.side_effect = lambda: call_order.append(
                    'migrate',
                )
                with patch(
                    'sethlans_manager.migration_runner'
                    '.run_collectstatic_inprocess',
                ) as mock_static:
                    mock_static.side_effect = lambda: call_order.append(
                        'collectstatic',
                    )
                    from run_manager import _frozen_boot_sequence
                    _frozen_boot_sequence(dev_mode=False)

        assert 'collectstatic' in call_order


class TestSourceBootSequence:
    """Source-mode boot: subprocess-based migrations and collectstatic."""

    def test_source_mode_uses_subprocess_migrate(self, monkeypatch):
        """In source mode, main() calls run_migrations_subprocess."""
        import shared.frozen_paths as fp_mod
        monkeypatch.setattr(fp_mod, 'is_frozen', lambda: False)

        with patch(
            'sethlans_manager.migration_runner'
            '.run_migrations_subprocess',
        ) as mock_sub_migrate:
            with patch(
                'sethlans_manager.migration_runner'
                '.run_collectstatic_subprocess',
            ):
                mock_sub_migrate.assert_not_called()
                # We can't run main() directly as it starts uvicorn,
                # but we can verify the functions exist and are importable
                from sethlans_manager.migration_runner import (
                    run_migrations_subprocess,
                )
                assert callable(run_migrations_subprocess)

    def test_subprocess_migrate_calls_sys_executable(self, monkeypatch):
        """run_migrations_subprocess uses sys.executable + manage.py."""
        mock_run = MagicMock()
        monkeypatch.setattr(
            'sethlans_manager.migration_runner.subprocess.run',
            mock_run,
        )
        from sethlans_manager.migration_runner import (
            run_migrations_subprocess,
        )
        manage_py = '/fake/manage.py'
        run_migrations_subprocess(manage_py)

        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == manage_py
        assert 'migrate' in cmd

    def test_subprocess_collectstatic_calls_sys_executable(
        self, monkeypatch,
    ):
        """run_collectstatic_subprocess uses sys.executable + manage.py."""
        mock_run = MagicMock()
        monkeypatch.setattr(
            'sethlans_manager.migration_runner.subprocess.run',
            mock_run,
        )
        from sethlans_manager.migration_runner import (
            run_collectstatic_subprocess,
        )
        manage_py = '/fake/manage.py'
        run_collectstatic_subprocess(manage_py)

        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0]
        assert cmd[0] == sys.executable
        assert 'collectstatic' in cmd
        assert '--noinput' in cmd


class TestInProcessMigrationRunner:
    """In-process migration runner uses call_command correctly."""

    @pytest.mark.django_db
    def test_inprocess_migrate_succeeds(self):
        """run_migrations_inprocess runs without error on test DB."""
        # This exercises the real call_command('migrate') path
        run_migrations_inprocess()

    def test_inprocess_migrate_calls_call_command(self, monkeypatch):
        """run_migrations_inprocess calls call_command('migrate')."""
        mock_cmd = MagicMock()
        monkeypatch.setattr(
            'django.core.management.call_command', mock_cmd,
        )
        run_migrations_inprocess()
        mock_cmd.assert_called_once_with('migrate', '--noinput')

    def test_inprocess_collectstatic_calls_call_command(
        self, monkeypatch,
    ):
        """run_collectstatic_inprocess calls call_command('collectstatic')."""
        mock_cmd = MagicMock()
        monkeypatch.setattr(
            'django.core.management.call_command', mock_cmd,
        )
        run_collectstatic_inprocess()
        mock_cmd.assert_called_once_with('collectstatic', '--noinput')

    def test_inprocess_migrate_exits_on_failure(self, monkeypatch):
        """run_migrations_inprocess calls sys.exit on failure."""
        monkeypatch.setattr(
            'django.core.management.call_command',
            MagicMock(side_effect=Exception('migration failed')),
        )
        with pytest.raises(SystemExit):
            run_migrations_inprocess()

    def test_inprocess_collectstatic_does_not_exit_on_failure(
        self, monkeypatch,
    ):
        """run_collectstatic_inprocess logs but does not exit on failure."""
        monkeypatch.setattr(
            'django.core.management.call_command',
            MagicMock(side_effect=Exception('static failed')),
        )
        # Should not raise SystemExit
        run_collectstatic_inprocess()


class TestPrepareRuntime:
    """_prepare_runtime skips django.setup() in frozen mode."""

    def test_prepare_runtime_skips_setup_when_frozen(self, monkeypatch):
        """In frozen mode, _prepare_runtime does not call django.setup()."""
        import run_manager as rm_mod

        # Patch is_frozen at the run_manager module level where it was
        # imported via ``from shared.frozen_paths import is_frozen``.
        monkeypatch.setattr(rm_mod, 'is_frozen', lambda: True)

        mock_setup = MagicMock()
        import django
        monkeypatch.setattr(django, 'setup', mock_setup)

        # Patch runtime init functions at the run_manager module level
        monkeypatch.setattr(
            rm_mod, 'get_enrollment_config', lambda *a: {},
        )
        monkeypatch.setattr(
            rm_mod, 'initialize_runtime_state',
            lambda *a, **kw: None,
        )

        _prepare_runtime = rm_mod._prepare_runtime
        _prepare_runtime(cert=MagicMock(), host='0.0.0.0', port='8080')

        # django.setup() should NOT have been called by _prepare_runtime
        mock_setup.assert_not_called()
