# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``manage.py unreal_fingerprint <share-relative path>`` (Unreal foundation
FR-MS-4, D3 scaffolding).

Prints the pin an admin supplies when adding an Unreal asset:

* for a ``.zip``: ``archive_sha256=<sha256 of the file's bytes>``;
* for a directory: ``overlay_sha256=<overlay digest>``, computed with
  ``shared.unreal.overlay_digest`` (the function the worker uses).

The path is relative to the manager's Sethlans share and is resolved
under it with ``resolve_in_share``.  Files are opened read-only; nothing
is written to the share.  UNC paths open only on Windows, so in phase 1
the command runs on a Windows manager host installed from source.  It is
temporary: in v1 the zip builder generates the pins.
"""

import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from shared.unreal.overlay_digest import OverlayDigestError, compute_overlay_digest
from shared.unreal.share_paths import resolve_in_share, validate_share_relpath

from workers.services.sethlans_share import configured_share_root

ATTESTATION_REMINDER = (
    "The fingerprint attests only to the file currently on the share. "
    "Compute it from the copy you built or verified (the packager's "
    "`archive_sha256`, `Get-FileHash` on the build machine before copying, "
    "or `python -m shared.unreal.overlay_digest` on the local overlay) "
    "unless only farm admins can write to the share."
)
_CHUNK = 8 * 1024 * 1024


def default_path_resolver(relpath):
    """Resolve a validated share-relative path under the manager's share.

    Raises:
        CommandError: the share is not configured, or the path does not
            resolve inside it.
    """
    root = configured_share_root()
    if root is None:
        raise CommandError(
            "The Sethlans share is not configured (set it with PUT /api/sethlans-share/)."
        )
    try:
        return Path(resolve_in_share(root, relpath).canonical)
    except ValueError as exc:
        raise CommandError(f"Invalid path: {exc}") from None


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = (
        "Print archive_sha256 (for a .zip) or overlay_sha256 (for a directory) "
        "of a path relative to the Sethlans share. " + ATTESTATION_REMINDER
    )
    # ``call_command`` accepts only parser options; tests pass a resolver.
    stealth_options = ('path_resolver',)

    def add_arguments(self, parser):
        parser.add_argument('relpath', help="Path relative to the Sethlans share root.")

    def handle(self, *args, **options):
        relpath = options['relpath']
        is_zip = relpath.lower().endswith('.zip')
        try:
            relpath = validate_share_relpath(
                relpath, kind='file' if is_zip else 'dir', suffix='.zip' if is_zip else None,
            )
        except ValueError as exc:
            raise CommandError(f"Invalid path: {exc}") from None
        resolver = options.get('path_resolver') or default_path_resolver
        path = Path(resolver(relpath))
        try:
            if is_zip:
                if not path.is_file():
                    raise CommandError(f"Not a file on the share: {relpath}")
                line = f"archive_sha256={_sha256_file(path)}"
            else:
                if not path.is_dir():
                    raise CommandError(f"Not a directory on the share: {relpath}")
                line = f"overlay_sha256={compute_overlay_digest(path).digest}"
        except (OSError, OverlayDigestError, ValueError) as exc:
            raise CommandError(f"Cannot fingerprint {relpath}: {exc}") from None
        self.stdout.write(line)
        self.stdout.write(ATTESTATION_REMINDER)
