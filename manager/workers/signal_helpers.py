# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

# Opt-in toggle to delete old thumbnails before saving new ones
DELETE_OLD_THUMBS = getattr(settings, "WORKERS_DELETE_OLD_THUMBNAILS", False)


def _filelike_to_bytes(f):
    """
    Safely convert a file-like or raw bytes to bytes for saving into a FileField.
    """
    if f is None:
        return None
    if isinstance(f, (bytes, bytearray)):
        return bytes(f)
    if hasattr(f, "read"):
        pos = None
        try:
            if hasattr(f, "tell"):
                pos = f.tell()
            if hasattr(f, "seek"):
                f.seek(0)
        except Exception:
            pos = None
        data = f.read()
        try:
            if pos is not None and hasattr(f, "seek"):
                f.seek(pos)
        except Exception:
            pass
        return data
    return None


def _delete_existing_filefield(inst, field_name: str):
    """
    If configured, delete the existing file referenced by the FileField
    WITHOUT saving the model (to avoid recursive signals).
    """
    if not DELETE_OLD_THUMBS:
        return
    try:
        field = getattr(inst, field_name, None)
        if not field:
            return
        name = getattr(field, "name", None)
        if not name:
            return
        storage = field.storage
        try:
            if storage.exists(name):
                storage.delete(name)
        except Exception:
            logger.debug(
                "Non-fatal: could not delete old file for %s.%s",
                inst.__class__.__name__,
                field_name,
            )
    except Exception:
        logger.debug(
            "Non-fatal: error while preparing deletion for %s.%s",
            inst.__class__.__name__,
            field_name,
        )


def _save_thumbnails_for_instances(
    instances, *, sender, handler, thumb_content, field_name="thumbnail"
):
    """
    Disconnects the sender/handler to prevent recursion, then saves the same
    thumbnail bytes to each instance's <field_name>. Finally reconnects the handler.

    - Relies on the model field's `upload_to` function to generate the final path.
    - Optionally deletes the existing file first (if WORKERS_DELETE_OLD_THUMBNAILS=True).
    """
    if thumb_content is None:
        return

    data = _filelike_to_bytes(thumb_content)
    if not data:
        return

    post_save.disconnect(handler, sender=sender)
    try:
        for inst in instances:
            # Delete old file if configured
            _delete_existing_filefield(inst, field_name)

            # Pass a generic name; the `upload_to` function will generate the final descriptive path.
            getattr(inst, field_name).save("thumb.png", ContentFile(data), save=True)
    finally:
        post_save.connect(handler, sender=sender)
