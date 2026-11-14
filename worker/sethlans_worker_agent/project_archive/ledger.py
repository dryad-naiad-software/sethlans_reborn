# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Byte ledger of the Unreal cache (Unreal foundation FR-PA-11).

All fields are guarded by the cache's **index lock**; ``ByteLedger``
methods assume the caller holds it. ``Reservation`` takes the lock itself.

* ``pristine_total``: sizes of ``ready`` + ``quarantined`` entries;
* ``in_flight_reserved``: bytes reserved **but not yet written**. The
  copy, extract and working-copy loops call ``Reservation.consume(n)`` for
  every ``n`` bytes they write, and ``release()`` returns the remainder.
  Written bytes already show in ``disk_usage().free``, so keeping them
  reserved would count them twice (Conc R2-L4: at stage B the archive's
  stage-A reservation is already zero);
* ``pending_trash``: bytes renamed to ``trash/`` and not yet deleted.

A reservation of ``required`` bytes fits when
``free - in_flight_reserved - required >= min_free``; a populate also
needs ``pristine_total + declared <= cache_max``.
"""
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class ByteLedger:
    pristine_total: int = 0
    in_flight_reserved: int = 0
    pending_trash: int = 0
    in_flight_populate: int = 0  # declared bytes of populates not yet published (Conc C8)

    def disk_fits(self, free: int, required: int, min_free: int) -> bool:
        return free - self.in_flight_reserved - required >= min_free

    def cache_fits(self, populate_bytes: Optional[int], cache_max: int) -> bool:
        if populate_bytes is None:
            return True
        return self.pristine_total + self.in_flight_populate + populate_bytes <= cache_max


class Reservation:
    """Reserved-but-unwritten bytes; ``consume`` as bytes land, ``release`` the rest.

    A populate reservation also holds ``populate`` bytes against
    ``CACHE_MAX`` until it is released (after the publish has added them to
    ``pristine_total``), so two concurrent populates cannot both fit.
    """

    def __init__(self, lock: threading.Lock, ledger: ByteLedger, amount: int, populate: int = 0):
        self._lock = lock
        self._ledger = ledger
        self._remaining = max(int(amount), 0)
        self._populate = max(int(populate), 0)
        self.amount = self._remaining

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining

    def consume(self, written: int) -> None:
        with self._lock:
            take = min(max(int(written), 0), self._remaining)
            self._remaining -= take
            self._ledger.in_flight_reserved -= take

    def release(self) -> None:
        with self._lock:
            self._ledger.in_flight_reserved -= self._remaining
            self._ledger.in_flight_populate -= self._populate
            self._remaining = self._populate = 0

    def __enter__(self) -> 'Reservation':
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class NullReservation:
    """A zero-byte reservation (nothing to write)."""
    amount = 0
    remaining = 0

    def consume(self, written: int) -> None:
        pass

    def release(self) -> None:
        pass

    def __enter__(self) -> 'NullReservation':
        return self

    def __exit__(self, *exc) -> None:
        pass
