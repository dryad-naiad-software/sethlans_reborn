# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reset setup (FR-RS-1, FR-RS-2; owner decision Q4).

* :func:`reset_running`, from the tray while Sethlans runs (the tray
  asks for the confirmation):
  1. stops the worker, then the manager, the way Quit does;
  2. stops the broadcaster and deletes ``broadcaster_params.json``;
  3. removes ``[setup] completed_at`` and nothing else.
  The loop then enters ``SETUP`` with a fresh token.
* ``sethlans --reset-setup``: refused while Sethlans runs (the
  single-instance lock of this settings folder is held), with
  :data:`REFUSED` and nothing changed; otherwise :func:`reset_stopped`
  resets at once with no prompt (the flag is the confirmation), and the
  launcher starts into ``SETUP``.

Nothing is deleted but those two things (FR-RS-2): the database, the
data folder and every other setting stay.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from launcher import cascade, setup_state, supervision
from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME, broadcaster_params_dir
from launcher.loop_context import LoopContext

logger = logging.getLogger(__name__)

REFUSED = ("Sethlans is running. Use Reset in the tray, or quit Sethlans first. "
           "Nothing was changed.")
DONE = "Setup was reset. Sethlans starts setup now."


def reset_running(ctx: LoopContext) -> None:
    """Reset from the tray (module doc, steps 1-3)."""
    logger.info("Reset setup: stopping the worker and the manager")
    cascade.cascade_quit(ctx.worker, ctx.manager, None, second_quit_check=ctx.second_quit_check)
    ctx.worker = ctx.manager = None
    supervision.stop_broadcaster(ctx.params_dir)
    removed = setup_state.remove_completed_at()
    ctx.setup_kind = None
    ctx.token = None
    logger.info("Reset setup: completed_at %s", "removed" if removed else "was not set")


def reset_stopped(params_dir: Optional[Path] = None) -> bool:
    """``--reset-setup`` while nothing runs; True when ``completed_at`` was removed."""
    folder = broadcaster_params_dir() if params_dir is None else Path(params_dir)
    try:
        (folder / BROADCASTER_PARAMS_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete the broadcaster params in %s: %s", folder, exc)
    removed = setup_state.remove_completed_at()
    logger.info("Reset setup from the command line: completed_at %s", "removed" if removed else "was not set")
    return removed
