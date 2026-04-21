# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for ``post_save`` signal handlers that write thumbnails.

Phase 4 thread-safety rewrite
-----------------------------
The previous implementation used ``post_save.disconnect(handler,
sender=sender)`` / ``connect`` around the save loop to keep the handler
from recursing into itself.  That is correct under single-threaded
uvicorn because Django's signal dispatcher is a per-process registry —
but it is **globally visible**.  The moment threaded Waitress dispatches
two requests that both try to generate a thumbnail, Thread A's
``disconnect`` silently suppresses Thread B's legitimate signal fire.

Phase 4 replaces the disconnect/connect dance with a per-thread
"skip-me" flag held in ``threading.local()`` and surfaced as a
``contextmanager``.  Only the thread that opened the context sees the
flag; concurrent threads continue to process signals normally.

A plain bool is insufficient — a handler that legitimately wants to
re-enter the helper (e.g. a ``post_save`` handler on Animation that
itself triggers a thumbnail refresh on its parent) would clobber the
outer flag on exit.  We use a **depth counter** instead: increment on
enter, decrement on exit, handlers early-return while depth > 0.  The
``finally`` in ``_skip_thumbnail_signals`` guarantees the counter is
restored even if an inner save raises, so the next call on the same
thread is unaffected by a prior failure.
"""

import logging
import threading
from contextlib import contextmanager

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# Opt-in toggle to delete old thumbnails before saving new ones
DELETE_OLD_THUMBS = getattr(settings, "WORKERS_DELETE_OLD_THUMBNAILS", False)


# ---------------------------------------------------------------------
# Per-thread re-entry guard.
#
# ``_tls.depth`` is an int >= 0.  Handlers early-return while > 0; a
# ``with _skip_thumbnail_signals():`` block increments it on enter and
# decrements on exit (via ``finally``).  Thread-local state means one
# thread's guard never masks another thread's signal.
# ---------------------------------------------------------------------
_tls = threading.local()


@contextmanager
def _skip_thumbnail_signals():
    """Suppress thumbnail-generating handlers on THIS thread.

    Use around any save-loop whose saved fields would otherwise
    re-fire the same thumbnail handler.  Reentrant: nested ``with``
    blocks stack depth, so inner blocks do not accidentally re-enable
    signal handling while an outer block is still active.

    Exception safety: the ``finally`` restores depth even if the
    wrapped block raises, so the next call on the same thread starts
    with a clean (zero) depth count.
    """
    if not hasattr(_tls, "depth"):
        _tls.depth = 0
    _tls.depth += 1
    try:
        yield
    finally:
        # Decrement rather than reset so nested use is safe.
        _tls.depth -= 1
        if _tls.depth < 0:
            # Defensive: never go negative.  An underflow would mean
            # a handler decremented without a matching increment,
            # which is a bug — log once and clamp so we don't poison
            # the thread's future guards.
            logger.warning(
                "signal_helpers: thumbnail skip-depth underflow "
                "clamped to 0 (thread=%r)",
                threading.current_thread().name,
            )
            _tls.depth = 0


def _skip_thumbnails() -> bool:
    """Return True if the current thread is inside a skip context."""
    return getattr(_tls, "depth", 0) > 0


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
    """Save the same thumbnail bytes to each instance's <field_name>.

    Wraps the save loop in ``_skip_thumbnail_signals()`` so that the
    inner ``.save()`` calls do NOT re-enter the calling ``post_save``
    handler on THIS thread.  Concurrent saves on OTHER threads
    continue to be processed normally — this is the core Phase 4
    thread-safety contract.

    Parameters
    ----------
    sender, handler : deprecated, kept for backward compatibility
        Formerly used for ``post_save.disconnect(handler, sender=sender)``.
        Phase 4 no longer touches the dispatcher; these parameters are
        still accepted so existing callsites (and tests that pass them
        by keyword) do not need to change in this patch.
    """
    if thumb_content is None:
        return

    data = _filelike_to_bytes(thumb_content)
    if not data:
        return

    # Silence unused-arg warnings while preserving the public signature.
    del sender
    del handler

    with _skip_thumbnail_signals():
        for inst in instances:
            # Delete old file if configured
            _delete_existing_filefield(inst, field_name)

            # Pass a generic name; the `upload_to` function will generate
            # the final descriptive path.
            getattr(inst, field_name).save(
                "thumb.png", ContentFile(data), save=True,
            )
