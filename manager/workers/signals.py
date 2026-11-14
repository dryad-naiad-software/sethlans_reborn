# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import threading  # noqa: F401  (tests patch ``workers.signals.threading.Thread``)

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    Job,
    AnimationFrame,
    Animation,
    TiledJob,
    Project,
    Asset,  # Import Asset
)
from .manifest_generator import update_project_manifest
from .parent_dispatch import (  # noqa: F401  (_trigger_video_assembly re-exported)
    _trigger_video_assembly,
    dispatch_for_job,
    dispatch_tiled_animation,
)
from .signal_helpers import _skip_thumbnails

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Project)
@receiver(post_save, sender=Asset)  # FIX: Added Asset as a sender
@receiver(post_save, sender=Job)
@receiver(post_save, sender=Animation)
@receiver(post_save, sender=TiledJob)
def handle_manifest_update(sender, instance, created, **kwargs):
    """
    Signal handler to create or update the project manifest file.

    This function is triggered whenever a Project is created or a new Job of any
    type (Job, Animation, TiledJob) is added to a project. It calls a utility
    to regenerate the `manifest.txt` file, ensuring it stays up-to-date.

    Args:
        sender (Model): The model class that sent the signal.
        instance (Model): The actual instance being saved.
        created (bool): True if a new record was created.
        **kwargs: Additional keyword arguments.
    """
    if not created:
        return

    project_id = None
    if sender is Project:
        project_id = instance.id
    elif hasattr(instance, 'project') and instance.project:
        project_id = instance.project.id
    elif hasattr(instance, 'asset') and instance.asset and instance.asset.project:
        project_id = instance.asset.project.id

    if project_id:
        logger.debug(f"Signal received from {sender.__name__} for project {project_id}. Updating manifest.")
        # robust (#225): a manifest error must not drop later callbacks.
        transaction.on_commit(lambda pid=project_id: update_project_manifest(pid), robust=True)
    else:
        logger.warning(f"Could not determine project ID from saved {sender.__name__} instance. Manifest not updated.")


@receiver(post_save, sender=Job)
def handle_job_completion(sender, instance, **kwargs):
    """
    - Update parent Animation/TiledJob/AnimationFrame status on any child save.
    - Trigger thumbnail generation for standard jobs' output files.
    """
    # Phase 4: early-return when the current thread is inside a
    # ``_skip_thumbnail_signals()`` context.  This replaces the pre-Phase-4
    # ``post_save.disconnect(handler, sender=Job)`` dance that silently
    # suppressed signals across ALL threads.  The guard keeps the inner
    # thumbnail-write saves from re-entering this handler without
    # affecting concurrent Job saves on other threads.
    if _skip_thumbnails():
        return

    from .image_utils import generate_thumbnail

    # Issue #219: recompute the parent on every child save.
    dispatch_for_job(instance)

    # --- Thumbnail Generation for Standard (non-frame, non-tiled) Jobs ---
    _maybe_generate_job_thumbnail(instance, generate_thumbnail)


def _maybe_generate_job_thumbnail(instance, generate_thumbnail):
    """Schedule a server-side thumbnail generation for a standard Job.

    Issue #118: the actual generate + save pair runs in a
    ``transaction.on_commit`` callback so the outer ``output_file.save``
    write lock on ``workers_job`` is released before Pillow resize + inner
    ``thumbnail.save`` run.  Implementation lives in
    ``thumbnail_signals.schedule_job_thumbnail``.
    """
    from .thumbnail_signals import schedule_job_thumbnail
    schedule_job_thumbnail(
        instance, generate_thumbnail, handle_job_completion,
    )


@receiver(post_save, sender=AnimationFrame)
def handle_animation_frame_completion(sender, instance, **kwargs):
    """
    - Update parent Animation status from its frames (QUEUED/RENDERING/ERROR).
    - When all frames are DONE, mark Animation DONE, set total time and completed_at,
      and trigger video assembly.  An animation with a failed frame goes to ERROR
      (once no frame is still in progress) and never assembles a video.
    - Generate thumbnails for frame and ALWAYS refresh the parent animation thumbnail
      to provide a visual progress indicator during rendering.
    """
    # Phase 4: per-thread skip guard (see ``handle_job_completion``).
    if _skip_thumbnails():
        return

    from .image_utils import generate_thumbnail
    from .thumbnail_signals import schedule_animation_frame_thumbnail

    # --- Thumbnail Generation (progress thumbnails) ---
    # The image_assembler is responsible for creating the AnimationFrame's
    # own thumbnail.  This signal's job is to always update the PARENT
    # animation's thumbnail to show the latest progress.  Issue #118:
    # generation is deferred via ``transaction.on_commit``.
    schedule_animation_frame_thumbnail(
        instance, generate_thumbnail, handle_animation_frame_completion,
    )

    # --- Parent Animation Status Update (issue #219) ---
    # DONE when every frame is DONE, ERROR once every frame is DONE/ERROR
    # with at least one ERROR, otherwise QUEUED/RENDERING.
    dispatch_tiled_animation(instance.animation_id)
