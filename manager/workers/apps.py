# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.apps import AppConfig


class WorkersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workers"

    def ready(self):
        # Ensure signal handlers are registered when the app loads
        from . import signals  # noqa: F401

        # Populate Blender series cache in the background
        import threading
        from .utils.blender_series_cache import populate_cache
        threading.Thread(target=populate_cache, daemon=True).start()
