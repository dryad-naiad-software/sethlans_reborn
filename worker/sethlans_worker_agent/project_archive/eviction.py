# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Disk-budget reservations with LRU eviction (Unreal foundation FR-PA-11).

``reserve(cache, required, populate_bytes=None)``:

1. Under the index lock, read ``disk_usage().free`` (a local call); if
   ``free - in_flight_reserved - required >= MIN_FREE`` (and, for a
   populate, ``pristine_total + in_flight_populate + populate_bytes <=
   CACHE_MAX``) the bytes are reserved at once.
2. Otherwise, still under the lock, LRU (``last_used``) entries with
   refcount 0 are chosen until their recorded sizes would make it fit;
   they are marked ``evicting`` and dropped from the index. If even all
   idle entries would not be enough, nothing is evicted.
3. Outside the lock the victims are renamed to ``trash/`` and deleted
   **synchronously**; a failed delete keeps its bytes in
   ``pending_trash`` and is retried by later reservations.
4. ``free`` is read **once** more and the check repeated; if it still does
   not fit, ``InsufficientDiskError`` is raised.

Eviction is never decided on an unchanged ``free`` value, so it cannot
over-evict. Callers hold at most their own populate lock (FR-PA-10).
"""
from typing import List, Optional

from sethlans_worker_agent.project_archive.cache import ArchiveCache, Entry
from sethlans_worker_agent.project_archive.errors import InsufficientDiskError
from sethlans_worker_agent.project_archive.ledger import NullReservation, Reservation


def _fits_locked(cache: ArchiveCache, free: int, required: int, populate_bytes: Optional[int]) -> bool:
    ledger = cache.ledger
    return (ledger.disk_fits(free, required, cache.limits.min_free_bytes)
            and ledger.cache_fits(populate_bytes, cache.limits.cache_max_bytes))


def _select_victims_locked(cache: ArchiveCache, free: int, required: int,
                           populate_bytes: Optional[int]) -> List[Entry]:
    ledger, limits = cache.ledger, cache.limits
    chosen: List[Entry] = []
    freed = 0
    for entry in cache.lru_candidates_locked():
        chosen.append(entry)
        freed += entry.size_bytes
        disk_ok = free + freed - ledger.in_flight_reserved - required >= limits.min_free_bytes
        cache_ok = (populate_bytes is None
                    or ledger.pristine_total + ledger.in_flight_populate - freed + populate_bytes
                    <= limits.cache_max_bytes)
        if disk_ok and cache_ok:
            return chosen
    return []


def _new_reservation_locked(cache: ArchiveCache, required: int, populate_bytes: Optional[int]) -> Reservation:
    populate = populate_bytes or 0
    cache.ledger.in_flight_reserved += required
    cache.ledger.in_flight_populate += populate
    return Reservation(cache.lock, cache.ledger, required, populate)


def reserve(cache: ArchiveCache, required: int, *, populate_bytes: Optional[int] = None):
    """Reserve ``required`` bytes (evicting if needed); raises ``InsufficientDiskError``.

    ``free`` is read under the index lock, so the read and the decision
    cannot interleave with another reservation (Conc C8).
    """
    required = max(int(required), 0)
    if required == 0 and populate_bytes is None:
        return NullReservation()
    cache.retry_pending_trash()
    with cache.lock:
        free = cache.free_bytes()
        if _fits_locked(cache, free, required, populate_bytes):
            return _new_reservation_locked(cache, required, populate_bytes)
        removals = cache.begin_eviction_locked(_select_victims_locked(cache, free, required, populate_bytes))
    if removals:
        cache.dispose(removals)
    with cache.lock:
        free = cache.free_bytes()
        if _fits_locked(cache, free, required, populate_bytes):
            return _new_reservation_locked(cache, required, populate_bytes)
        available = free - cache.ledger.in_flight_reserved - cache.limits.min_free_bytes
    raise InsufficientDiskError(required, available, cache.limits.cache_max_bytes)
