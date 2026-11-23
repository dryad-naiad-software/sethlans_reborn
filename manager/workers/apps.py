# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.apps import AppConfig


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

        # Populate the Blender series cache in the background — only in
        # a process that serves requests (issue #292).  ready() also runs
        # for every management command, so here it starts the fetch for
        # ``runserver``'s serving process alone; the Waitress serving
        # process starts it from ``waitress_launcher.launch``.  Pytest
        # and ``SETHLANS_DISABLE_RELEASE_FETCH=1`` block it everywhere.
        # See ``workers.release_cache_startup``.
        from .release_cache_startup import start_if_runserver
        start_if_runserver()

        # Boot-time parts check (finds FFmpeg, may download it) — the
        # same gate (issue #294): ready() starts it for runserver's
        # serving process alone; the Waitress serving process starts
        # it from ``waitress_launcher.launch``.  Management commands,
        # the reloader's parent and the test runner never run it.
        # See ``workers.parts_check_startup``.
        from .parts_check_startup import start_if_runserver as start_parts_check_if_runserver
        start_parts_check_if_runserver()

        # Reset animation videos left ASSEMBLING by a stopped server — the
        # same gate (issue #295): ready() resets for runserver's serving
        # process alone; the Waitress serving process resets from
        # ``waitress_launcher.launch``.  A management command run beside a
        # live server must not mark its running builds failed.
        # See ``workers.animation_video_startup``.
        from .animation_video_startup import reset_if_runserver
        reset_if_runserver()
