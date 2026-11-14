# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
How an Unreal job ends, before it is turned into a ``RenderResult``
(Unreal foundation FR-UE-2, FR-UE-17, FR-UE-25, chunk 7 req. 15).

The phases raise one of these; ``job_run`` maps them:

* ``JobFailure(message)``: a content or render failure -> ``ERROR`` with
  ``message`` (a pilot's message gets the ``"Pilot render failed: "``
  prefix);
* ``LocalConflict(reason, message)``: a local, transient condition (a
  foreign listener on the Zen port, a failed known-folder check, the
  pilot's freed-memory or sampling-gap rule) -> ``release/`` with
  ``reason`` (``dispatch_failed`` before the first launch, else
  ``local_conflict``), ``released=True``;
* ``Cancelled(cause)``: ``'canceled'`` -> ``CANCELED``; ``'shutdown'`` ->
  ``release/`` with ``shutdown``; ``'reassigned'`` -> nothing reported;
* ``SurvivorsLeft(survivors)``: UE processes did not exit (FR-UE-11).
"""
from typing import Tuple

PILOT_PREFIX = "Pilot render failed: "


class JobFailure(Exception):
    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


class LocalConflict(Exception):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = str(message)
        super().__init__(self.message)


class Cancelled(Exception):
    def __init__(self, cause: str):
        self.cause = cause
        super().__init__(f"Unreal job stopped ({cause}).")


class SurvivorsLeft(Exception):
    def __init__(self, survivors: Tuple[Tuple[int, str], ...]):
        self.survivors = tuple(survivors)
        super().__init__(f"Unreal processes did not exit: {self.survivors}")


def pilot_message(message: str) -> str:
    """FR-UE-25 "Failure": every pilot failure reads ``Pilot render failed: <reason>``."""
    return message if message.startswith(PILOT_PREFIX) else PILOT_PREFIX + message
