# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Errors raised by the project-archive pipeline (Unreal foundation FR-PA,
FR-UE-17).

Two families matter to the executor:

* ``ArchiveError`` is a **content or local** failure: the job goes to
  ERROR with ``str(exc)`` as its message (pin mismatch, unsafe archive,
  size caps, a changed archive, disk budget, key collision, ...).
* ``ShareUnavailableError`` is **environmental**: the share stalled or
  failed before UE launched. The executor releases the job with
  ``share_unavailable`` and marks the share unreachable (FR-UE-17).

``AbortedError`` reports a cancel or a shutdown seen by a phase loop; its
``cause`` is ``'canceled'`` or ``'shutdown'``.
"""


class ArchiveError(Exception):
    """A content or local failure; ``str(exc)`` is the job's error message."""


class UnsafeArchiveError(ArchiveError):
    """The archive (or one entry) breaks an extraction rule (FR-PA-6/7)."""

    def __init__(self, reason: str, entry: str = ''):
        self.reason = reason
        self.entry = entry
        text = f"Unsafe Unreal project archive: {reason}"
        if entry:
            text += f" (entry {entry[:200]!r})"
        super().__init__(text)


class UnsafeOverlayError(ArchiveError):
    """The overlay directory breaks a staging rule (FR-PA-12)."""


class ArchiveTooLargeError(ArchiveError):
    """The share file exceeds ``MAX_ARCHIVE_GB`` (checked before any read)."""


class ArchiveChangedError(ArchiveError):
    """The share file changed or grew while it was being copied (FR-PA-4)."""


class ArchivePinMismatchError(ArchiveError):
    """The copied bytes do not hash to the asset's pin (FR-PA-5, D3)."""

    def __init__(self, pin: str, got: str):
        self.pin, self.got = pin, got
        super().__init__(f"Archive SHA-256 mismatch: expected {pin[:16]}…, got {got[:16]}…; "
                         "the share file differs from the pinned asset.")


class OverlayPinMismatchError(ArchiveError):
    """The staged overlay's digest differs from ``overlay_sha256`` (FR-PA-12)."""

    def __init__(self, pin: str, got: str):
        self.pin, self.got = pin, got
        super().__init__(f"Overlay SHA-256 mismatch: expected {pin[:16]}…, got {got[:16]}….")


class CacheKeyCollisionError(ArchiveError):
    """``p/<key16>`` holds a different archive that a job is still using (FR-PA-8)."""

    def __init__(self, key16: str):
        super().__init__(f"Unreal cache key collision on {key16}; retry later.")


class InsufficientDiskError(ArchiveError):
    """A disk reservation does not fit even after eviction (FR-PA-11)."""

    def __init__(self, need: int, available: int, budget: int):
        gib = 1024 ** 3
        super().__init__(
            f"Insufficient local disk for Unreal project cache: need {need / gib:.1f} GiB, "
            f"available {max(available, 0) / gib:.1f} GiB (budget {budget / gib:.1f} GiB).")


class UprojectNotFoundError(ArchiveError):
    """``uproject_path`` names no file in the archive (FR-PA-20)."""

    def __init__(self, uproject: str):
        super().__init__(f"uproject '{uproject}' not found in archive")


class WorkingCopyError(ArchiveError):
    """The working copy cannot be built (stuck old copy, MAX_PATH, ...)."""


class CacheUnavailableError(ArchiveError):
    """``CACHE_DIR`` failed its owner-only or reparse check; Unreal must be disabled."""


class PrepTimeoutError(ArchiveError):
    """``PREP_TIMEOUT_SECONDS`` elapsed during copy, extract, overlay or working copy."""


class ShareUnavailableError(Exception):
    """The share failed before UE launched; release the job (FR-UE-17)."""


class ShareStallError(ShareUnavailableError):
    """No bytes arrived from the share for ``COPY_STALL_SECONDS`` (FR-PA-4)."""


class AbortedError(Exception):
    """A cancel or shutdown was observed; ``cause`` is 'canceled' or 'shutdown'."""

    def __init__(self, cause: str):
        self.cause = cause
        super().__init__(f"Unreal job preparation stopped ({cause}).")
