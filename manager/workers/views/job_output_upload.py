# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Store a worker's render output and attach it to the job (issue #217).

Files are written to storage first, outside any transaction, so slow disk
I/O never runs under the job row lock.  The DB write then runs under the
lock in :func:`attach_worker_output`, which re-checks that the job still
belongs to the worker.  If that check fails (the job was canceled or
requeued meanwhile), or the write fails, the files just stored are
deleted again unless the job row references them, so a rejected upload
leaves no orphan file behind.
"""

import logging

from PIL import Image

from ..models import Job
from ..services.worker_job_writes import (
    attach_worker_output, ensure_worker_can_write,
)
from .upload_helpers import sanitize_filename

logger = logging.getLogger(__name__)

# Maximum size for worker-provided thumbnails (1 MB)
MAX_THUMBNAIL_SIZE = 1 * 1024 * 1024


def store_worker_thumbnail(job, thumb_file):
    """Validate a worker-provided thumbnail and write it to storage.

    Sets ``job.thumbnail`` in memory only (``save=False``).  An invalid
    thumbnail is logged and skipped; it never fails the upload.

    Returns:
        The stored file name, or None if the thumbnail was skipped.
    """
    try:
        if thumb_file.size > MAX_THUMBNAIL_SIZE:
            logger.warning(
                "Worker-provided thumbnail for job %s failed "
                "validation: file size %d bytes exceeds 1MB limit. "
                "Saving output without thumbnail.",
                job.id, thumb_file.size,
            )
            return None

        thumb_file.seek(0)
        try:
            with Image.open(thumb_file) as img:
                img.verify()
        except Exception:
            logger.warning(
                "Worker-provided thumbnail for job %s failed "
                "validation: not a valid image file. "
                "Saving output without thumbnail.",
                job.id,
            )
            return None
        thumb_file.seek(0)

        job.thumbnail.save(
            sanitize_filename(thumb_file.name), thumb_file, save=False,
        )
        return job.thumbnail.name
    except Exception as e:
        logger.warning(
            "Worker-provided thumbnail for job %s failed "
            "validation: %s. Saving output without thumbnail.",
            job.id, e,
        )
        return None


def _discard_unreferenced(job_pk, stored):
    """Delete stored files that the job row does not reference.

    ``stored`` maps a Job file field name to the storage name written for
    it.  A name the row references is kept: that only happens when the
    write committed and a later step (an ``on_commit`` callback) raised.
    """
    for field, name in stored.items():
        if not name:
            continue
        try:
            if Job.objects.filter(pk=job_pk, **{field: name}).exists():
                continue
            Job._meta.get_field(field).storage.delete(name)
        except Exception:
            logger.exception(
                "Could not remove rejected upload file %s for job %s.",
                name, job_pk,
            )


def store_and_attach_output(job, worker, file_obj, thumb_file=None):
    """Store the output (and optional thumbnail) and attach it to ``job``.

    Args:
        job: The job as read (unlocked) by the view; used for the upload
            paths and a fail-fast ownership check.
        worker: The calling ``Worker``.
        file_obj: The validated output upload.
        thumb_file: Optional worker-provided thumbnail upload.

    Returns:
        The updated ``Job`` read under the row lock.

    Raises:
        JobWriteConflict: the job no longer accepts output from ``worker``.
            No file is left in storage.
    """
    # Fail fast before writing anything when the copy is already stale.
    ensure_worker_can_write(job, worker)

    stored = {}
    try:
        if thumb_file:
            stored['thumbnail'] = store_worker_thumbnail(job, thumb_file)
        job.output_file.save(
            sanitize_filename(file_obj.name), file_obj, save=False,
        )
        stored['output_file'] = job.output_file.name
        return attach_worker_output(
            job.pk, worker, stored['output_file'], stored.get('thumbnail'),
        )
    except BaseException:
        _discard_unreferenced(job.pk, stored)
        raise
