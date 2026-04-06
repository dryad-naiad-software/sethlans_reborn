# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class WorkersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workers"

    ffmpeg_detected: bool = False

    def ready(self):
        # Ensure signal handlers are registered when the app loads
        from . import signals  # noqa: F401

        # Populate Blender series cache in the background
        import threading
        from .utils.blender_series_cache import populate_cache
        threading.Thread(target=populate_cache, daemon=True).start()

        # Detect ffmpeg availability
        self._detect_ffmpeg()

        # Reset stuck video assemblies from prior server shutdown
        self._reset_stuck_assemblies()

    def _detect_ffmpeg(self):
        from .utils.ffmpeg_utils import ffmpeg_available, ffmpeg_path

        if ffmpeg_available():
            path = ffmpeg_path()
            WorkersConfig.ffmpeg_detected = True
            logger.info("FFmpeg detected at %s. Video assembly is available.", path)
        else:
            WorkersConfig.ffmpeg_detected = False
            logger.warning("FFmpeg not found on PATH. Video assembly will be disabled.")

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
