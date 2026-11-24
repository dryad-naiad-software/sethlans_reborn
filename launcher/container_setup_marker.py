# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setup-complete marker for the manager Docker image (issue #301).

The heartbeat tells workers whether the manager is set up, from the
``.setup_complete`` marker in the manager data folder
(``workers.services.sentinel``); workers take no work while it says no.
On a native install the setup wizard writes the marker. The image has
no wizard: it is configured by environment variables, so the container
counts as set up as soon as it starts. :func:`setup_marker_problem`
runs in the entrypoint after the data-folder check and before the
manager starts:

* A valid, completed marker is left as it is.
* No marker, or one that is not a valid completed record (an empty
  file, malformed JSON, an unknown version, an unfinished wizard
  record): write a completed one and log it.
* The write fails: exit :data:`EXIT_SETUP_MARKER_FAILED` with a
  one-paragraph message. Starting anyway would leave a healthy-looking
  container whose workers idle with no error anywhere. If the error
  came after the marker was already in place (folder fsync or chmod),
  the marker is valid: log a warning and start.

The marker format is the one ``workers.services.sentinel`` defines;
that module is stdlib-only, so importing it here does not load Django.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from workers.services.sentinel import (
    SENTINEL_FILENAME,
    create_sentinel,
    is_setup_complete,
)

logger = logging.getLogger("sethlans.container")

#: The setup-complete marker cannot be written (1 = start failure,
#: 2 = configuration error, 3 = data folder not writable).
EXIT_SETUP_MARKER_FAILED = 4

#: The container runs the manager only; there is no worker in it.
CONTAINER_TOPOLOGY = "manager"


def _failure_message(marker: Path, exc: OSError) -> str:
    reason = exc.strerror or exc.__class__.__name__
    return (
        f"The manager cannot write its setup-complete marker {marker} "
        f"({reason}). Without it, enrolled workers stay idle. Check that "
        f"the data folder {marker.parent} has free space and that "
        f"{marker} is not a folder or a file the manager cannot replace, "
        "then start the container again."
    )


def setup_marker_problem(data_dir: Path) -> Optional[Tuple[int, str]]:
    """Make sure ``data_dir`` holds a completed setup marker.

    Returns ``None`` when the marker is (now) in place, or
    ``(exit code, one-paragraph message)`` when it cannot be written.
    """
    marker = Path(data_dir) / SENTINEL_FILENAME
    if is_setup_complete(data_dir):
        logger.info("Setup-complete marker %s is present", marker)
        return None
    existed = os.path.lexists(marker)
    try:
        create_sentinel(data_dir, topology=CONTAINER_TOPOLOGY, checkpoints=[])
    except OSError as exc:
        # The writer renames the marker into place before it fsyncs the
        # folder and sets 0600; either of those can fail on some network
        # or idmapped mounts after the marker is already valid on disk.
        if not is_setup_complete(data_dir):
            return EXIT_SETUP_MARKER_FAILED, _failure_message(marker, exc)
        logger.warning(
            "Wrote setup-complete marker %s, but a step after the write "
            "failed (%s); the marker is valid, so starting anyway",
            marker, exc,
        )
        return None
    if existed:
        logger.warning(
            "Replaced %s: it was not a valid completed setup marker "
            "(empty, malformed, unknown version or unfinished)", marker,
        )
    else:
        logger.info(
            "Wrote setup-complete marker %s: the container is configured "
            "by its environment, so enrolled workers can take work", marker,
        )
    return None
