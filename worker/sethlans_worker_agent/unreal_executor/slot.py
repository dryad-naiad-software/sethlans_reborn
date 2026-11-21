# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal slots and their per-slot isolation (render per card FR-PC-SLOT-1,
FR-PC-SLOT-2; D5).

Every Unreal job holds one **slot** ``n`` (``0..SLOT_COUNT-1``) for as long
as its reservation lives. The slot decides every place two UE processes on
one machine would otherwise share:

========================  ==============================  =================================
what                      slot 0 (today's paths)          slot ``n >= 1``
========================  ==============================  =================================
farm profile ``P``        ``CACHE_DIR\\ueprofile``         ``CACHE_DIR\\ueprofile-s<n>``
(``USERPROFILE``,         (and its ``Temp``: ``TEMP``      (and its ``Temp``)
``LOCALAPPDATA`` ... )    and ``TMP``)
Zen data                  ``CACHE_DIR\\zen\\data``          ``CACHE_DIR\\zen-s<n>\\data``
Zen file-system DDC       ``CACHE_DIR\\zen\\fs``            ``CACHE_DIR\\zen-s<n>\\fs``
Zen port                  ``unreal.zen_port``             ``unreal.zen_port + n``
work copy                 ``w\\<key16><ov8>-s0``           ``w\\<key16><ov8>-s<n>``
warm-up marker            ``<build>.json``                ``<build>-s<n>.json``
========================  ==============================  =================================

UE reads its temp folder through ``GetTempPath`` (``TMP``, then ``TEMP``;
``WindowsPlatformProcess.cpp`` ``UserTempDir``), and the shader working
folder the first instance cleans is ``UserTempDir()/UnrealShaderWorkingDir/``
(``ShaderWorkingDir``), so a per-slot ``TEMP``/``TMP`` gives each slot its
own shader folder. Zen's install copy lives under ``UserSettingsDir()``
(``LOCALAPPDATA``) ``/UnrealEngine/Common/Zen/Install``
(``ZenServerInterface.cpp`` ``GetLocalInstallPath``), so each slot also runs
its own ``zenserver.exe`` path.

``SlotPool`` replaces the Reserver's ``BoundedSemaphore(1)``: ``take()``
gives the lowest free slot or ``None``; ``give(n)`` returns it. Its lock is
a leaf lock (NF-1). Giving back a slot that is not taken is a bug and
raises ``ValueError`` (as an over-released ``BoundedSemaphore`` does).
"""
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import user_state, zen_private

SLOT_COUNT = config_unreal.ZEN_PORT_SLOTS


@dataclass(frozen=True)
class Slot:
    """One slot's isolation paths and Zen port (D5)."""
    n: int
    cache_dir: str
    profile: str
    zen_data: str
    zen_fs: str
    zen_port: int


def slot_for(cache_dir, base_port: int, n: int) -> Slot:
    """Slot ``n``'s paths under ``cache_dir`` and its port ``base_port + n``. Raises ``ValueError``."""
    n = zen_private.check_slot(n)
    port = int(base_port) + n
    if port == zen_private.UE_DEFAULT_ZEN_PORT or not 1024 <= port <= 65535:
        raise ValueError(f"slot {n} would use Zen port {port}, which the farm must not use")
    return Slot(n=n, cache_dir=str(cache_dir), profile=user_state.profile_dir(cache_dir, n),
                zen_data=zen_private.zen_data_dir(cache_dir, n), zen_fs=zen_private.zen_fs_dir(cache_dir, n),
                zen_port=port)


def all_slots(cache_dir, base_port: int) -> Tuple[Slot, ...]:
    """Every slot ``0..SLOT_COUNT-1``, in order."""
    return tuple(slot_for(cache_dir, base_port, n) for n in range(SLOT_COUNT))


class SlotPool:
    """The free slots of one worker (FR-PC-SLOT-1)."""

    def __init__(self, count: int = SLOT_COUNT):
        self._slot_lock = threading.Lock()  # leaf lock (NF-1)
        self._count = int(count)
        self._free = set(range(self._count))

    def take(self) -> Optional[int]:
        """The lowest free slot, now taken; ``None`` when every slot is taken."""
        with self._slot_lock:
            if not self._free:
                return None
            n = min(self._free)
            self._free.discard(n)
            return n

    def give(self, n: int) -> None:
        """Return slot ``n``. Raises ``ValueError`` when ``n`` is not a taken slot of this pool."""
        with self._slot_lock:
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < self._count or n in self._free:
                raise ValueError(f"Unreal slot {n!r} is not taken; it cannot be given back")
            self._free.add(n)

    def free(self) -> Tuple[int, ...]:
        """The free slots, lowest first (a snapshot)."""
        with self._slot_lock:
            return tuple(sorted(self._free))
