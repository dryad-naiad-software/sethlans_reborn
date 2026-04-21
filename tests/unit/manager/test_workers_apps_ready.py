# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for ``WorkersConfig.ready`` guards (issue #97).

The Blender series cache populate thread previously fired
unconditionally during ``WorkersConfig.ready``, which meant every
pytest invocation hit ``download.blender.org`` and leaked
``I/O operation on closed file`` tracebacks during teardown. The
guard now gates the thread on an env var and on whether pytest is
active.
"""

from __future__ import annotations

from workers.apps import WorkersConfig


# ---------------------------------------------------------------------
# _should_populate_release_cache — guard logic.
# ---------------------------------------------------------------------

class TestShouldPopulateReleaseCache:

    def test_false_under_pytest_by_default(self, monkeypatch):
        """With the env var cleared, pytest presence in ``sys.modules``
        alone must be enough to keep the guard closed. Proves the
        pytest-detection branch stands on its own — it does not rely
        on the pytest.ini env flag also being set."""
        monkeypatch.delenv(
            'SETHLANS_DISABLE_RELEASE_FETCH', raising=False,
        )
        assert WorkersConfig._should_populate_release_cache() is False

    def test_false_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv('SETHLANS_DISABLE_RELEASE_FETCH', '1')
        assert WorkersConfig._should_populate_release_cache() is False

    def test_true_when_env_unset_and_pytest_absent(self, monkeypatch):
        """Simulate the production path: env unset, pytest not loaded."""
        import sys
        monkeypatch.delenv(
            'SETHLANS_DISABLE_RELEASE_FETCH', raising=False,
        )
        fake_modules = {
            k: v for k, v in sys.modules.items()
            if not k.startswith('pytest')
        }
        monkeypatch.setattr(sys, 'modules', fake_modules)
        assert WorkersConfig._should_populate_release_cache() is True

    def test_env_override_beats_pytest_presence(self, monkeypatch):
        """The env var dominates — if an operator explicitly disables
        the fetch, we never start the thread even if pytest happens to
        be importable in the same interpreter."""
        monkeypatch.setenv('SETHLANS_DISABLE_RELEASE_FETCH', '1')
        # pytest is in sys.modules as usual; don't strip it.
        assert WorkersConfig._should_populate_release_cache() is False

    def test_env_value_other_than_one_allows_fetch(self, monkeypatch):
        """Only the literal string ``'1'`` disables the fetch. Any
        other truthy-ish value (``'true'``, ``'yes'``, ``'0'``, '')
        does not count — this matches the convention the rest of the
        codebase uses for env flags."""
        import sys
        monkeypatch.setenv('SETHLANS_DISABLE_RELEASE_FETCH', 'true')
        fake_modules = {
            k: v for k, v in sys.modules.items()
            if not k.startswith('pytest')
        }
        monkeypatch.setattr(sys, 'modules', fake_modules)
        assert WorkersConfig._should_populate_release_cache() is True


# ---------------------------------------------------------------------
# populate_cache logger hardening — regression for I/O errors after
# stdout has closed during teardown.
# ---------------------------------------------------------------------

class TestSafeLog:

    def test_safe_log_swallows_value_error(self, mocker):
        """``ValueError: I/O operation on closed file`` must not leak
        out of ``_safe_log``. The populate thread is a daemon that
        may outlive the stdout handle (e.g. between pytest's session
        teardown and thread exit); raising here would litter stderr
        with scary tracebacks that look like real failures."""
        from workers.utils import blender_series_cache as cache

        mocker.patch.object(
            cache.logger, 'info',
            side_effect=ValueError('I/O operation on closed file.'),
        )
        # Must not raise.
        cache._safe_log('info', 'hello')

    def test_safe_log_swallows_os_error(self, mocker):
        """Same contract for ``OSError`` — stdlib sometimes raises this
        variant when a rotating file handler's target disappears."""
        from workers.utils import blender_series_cache as cache

        mocker.patch.object(
            cache.logger, 'exception',
            side_effect=OSError('stream closed'),
        )
        cache._safe_log('exception', 'boom')

    def test_safe_log_passes_args_through(self, mocker):
        """Printf-style args are forwarded verbatim."""
        from workers.utils import blender_series_cache as cache

        info = mocker.patch.object(cache.logger, 'info')
        cache._safe_log('info', 'populated: %s', ['4.0', '4.1'])
        info.assert_called_once_with('populated: %s', ['4.0', '4.1'])

    def test_safe_log_ignores_unknown_level(self, mocker):
        """``_safe_log`` is intentionally narrow — only 'info' and
        'exception' are wired up today. Unknown levels are silently
        dropped rather than raising, so bugs in the calling code do
        not poison the background thread."""
        from workers.utils import blender_series_cache as cache

        info = mocker.patch.object(cache.logger, 'info')
        exc = mocker.patch.object(cache.logger, 'exception')
        cache._safe_log('warning', 'ignored')
        info.assert_not_called()
        exc.assert_not_called()
