# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class WorkersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workers"

    def ready(self):
        # Apply our logging config.  Django's built-in logging bootstrap is
        # disabled (``LOGGING_CONFIG = None`` in settings) to avoid a crash
        # on frozen builds where ``AdminEmailHandler``'s import chain isn't
        # collected.  Calling ``configure()`` here covers entry points that
        # don't invoke it explicitly (manage.py, pytest, etc.); explicit
        # callers in ``run_manager.py`` remain idempotent.
        from sethlans_manager.logging_config import configure as _cfg
        _cfg()

        # Ensure signal handlers are registered when the app loads
        from . import signals  # noqa: F401

        # Phase 4: attach the SQLite WAL PRAGMA hook to Django's
        # ``connection_created`` signal.  No-op for non-sqlite vendors
        # so the Postgres / MySQL overlays are unaffected.
        from sethlans_manager.db_hooks import register_connection_hooks
        register_connection_hooks()

        # Populate Blender series cache in the background.
        #
        # Guarded against:
        #   * Test runs (``pytest`` in ``sys.modules``) — unit tests
        #     boot Django many times per run; we don't want 2 700 test
        #     invocations hitting ``download.blender.org``. Tests that
        #     need the cache populate it explicitly via their own
        #     fixtures / mocks.
        #   * Airgapped deployments — operators can set
        #     ``SETHLANS_DISABLE_RELEASE_FETCH=1`` to skip the fetch
        #     entirely (the cache stays empty; the settings page will
        #     show an empty series list until a manual refresh).
        if self._should_populate_release_cache():
            import threading
            from .utils.blender_series_cache import populate_cache
            threading.Thread(
                target=populate_cache,
                name='blender-series-cache-populate',
                daemon=True,
            ).start()

        # Boot-time parts-check.  Idempotent under multi-fire ready()
        # via the in-process ``_thread_started`` flag — autoreload
        # subprocess parents, management commands, the test runner,
        # and runserver's double-fire all become no-ops after the
        # first call.
        from .services import parts_check
        parts_check.run_parts_check()

        # Reset stuck video assemblies from prior server shutdown
        self._reset_stuck_assemblies()

    @staticmethod
    def _should_populate_release_cache() -> bool:
        """Gate the background Blender release fetch.

        Returns False when running under pytest or when the operator
        has set ``SETHLANS_DISABLE_RELEASE_FETCH=1``. Broken out as a
        static method so tests can patch it directly and assert the
        fetch thread is not spawned.
        """
        import os
        import sys
        if os.environ.get('SETHLANS_DISABLE_RELEASE_FETCH') == '1':
            return False
        if 'pytest' in sys.modules:
            return False
        return True

    def _reset_stuck_assemblies(self):
        from django.db import OperationalError, ProgrammingError
        from .models import Animation

        try:
            updated = Animation.objects.filter(
                video_status='ASSEMBLING'
            ).update(
                video_status='ERROR',
                video_error='Video assembly was interrupted by server restart.',
            )
            if updated:
                logger.info(
                    "Reset %d animation(s) from ASSEMBLING to ERROR "
                    "(server restart recovery).",
                    updated,
                )
        except (OperationalError, ProgrammingError):
            # Column does not exist yet (pre-migration). Safe to skip.
            pass
