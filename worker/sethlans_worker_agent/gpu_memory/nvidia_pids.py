# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``nvidia-smi -q -d PIDS`` parser (GPU memory reader FR-GN-3, D6).

The listing covers graphics (Vulkan, OpenGL) and compute processes, so a
Linux render is seen. Its documented layout, one block per card::

    Attached GPUs                         : 1
    GPU 00000000:01:00.0
        Processes
            GPU instance ID               : N/A
            Compute instance ID           : N/A
            Process ID                    : 1234
                Type                      : G
                Name                      : <the process path>
                Used GPU Memory           : 512 MiB

A card with no process shows ``Processes : None``.

Only ``Process ID`` and ``Used GPU Memory`` pairs are read, and only inside
a block that starts with a ``GPU <bus id>`` line; the bus id gives the card
key. ``Name`` values (process paths) and every other field are ignored and
never kept. A pair whose pid or memory is not an integer is dropped and
makes its card unattributable (a card is attributed only when every listed
process has a number), as do a pid without a memory line, a memory line
without a pid, a pid listed twice on the card and a second block for the
same card.

A process can put newlines into its own name and so fake lines. The whole
listing is therefore distrusted (``{}``: no card attributed) when the number
of ``GPU <bus id>`` lines differs from the ``Attached GPUs`` value given
once before the first block, or when a block's bus id is not among
``known_keys`` (the card query's keys).
"""
import re
from typing import Collection, Dict, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key

_GPU_LINE = re.compile(r'GPU\s+([0-9A-Fa-f]{4,8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7])')
_PID = re.compile(r'[0-9]{1,10}')
_MIB = re.compile(r'([0-9]{1,10})\s*MiB')
_BAD_PID = object()

# card key -> ((pid, used MiB), ...), or None when the card cannot be attributed
Listing = Dict[str, Optional[Tuple[Tuple[int, int], ...]]]


class _Block:
    """One card's process pairs while the listing is read."""

    def __init__(self):
        self.pairs: List[Tuple[int, int]] = []
        self.complete = True
        self.pid = None  # a pid waiting for its memory line, or _BAD_PID

    def field(self, name: str, value: str) -> None:
        if name == 'Process ID':
            if self.pid is not None:
                self.complete = False  # the previous pid had no memory line
            self.pid = int(value) if _PID.fullmatch(value) else _BAD_PID
        elif name == 'Used GPU Memory':
            match = _MIB.fullmatch(value)
            if self.pid is None or self.pid is _BAD_PID or match is None:
                self.complete = False
            elif any(pid == self.pid for pid, _used in self.pairs):
                self.complete = False  # one pid listed twice: a faked line, trust neither
            else:
                self.pairs.append((self.pid, int(match.group(1))))
            self.pid = None

    def result(self) -> Optional[Tuple[Tuple[int, int], ...]]:
        return tuple(self.pairs) if self.complete and self.pid is None else None


def _start(blocks: Dict[str, _Block], bus_id: str) -> Optional[_Block]:
    """The block a ``GPU <bus id>`` line opens; ``None`` (pairs ignored) when the id gives no card key."""
    key = card_key(bus_id)
    if key is None:
        return None
    if key in blocks:
        blocks[key].complete = False  # the same card listed twice: trust neither
        return blocks[key]
    blocks[key] = _Block()
    return blocks[key]


def _attached(name: str, value: str, attached: Optional[int]) -> Optional[int]:
    """The ``Attached GPUs`` count from a header line; -1 (never matches) when it is given twice or is not a number."""
    if name != 'Attached GPUs':
        return attached
    return int(value) if attached is None and _PID.fullmatch(value) else -1


def parse_pids(stdout: str, known_keys: Collection[str]) -> Listing:
    """Per card key, the ``(pid, used MiB)`` pairs listed on it, or ``None`` when it cannot be attributed.

    ``{}`` (no card attributed) when the listing is distrusted (module doc).
    """
    blocks: Dict[str, _Block] = {}
    block: Optional[_Block] = None
    attached, gpu_lines = None, 0
    for line in stdout.splitlines():
        text = line.strip()
        match = _GPU_LINE.fullmatch(text)
        name, _sep, value = text.partition(':')
        if match is not None:
            gpu_lines += 1
            block = _start(blocks, match.group(1))
            if block is None or card_key(match.group(1)) not in known_keys:
                return {}  # a bus id the card query does not list
        elif block is not None:
            block.field(name.strip(), value.strip())
        elif gpu_lines == 0:
            attached = _attached(name.strip(), value.strip(), attached)
    if attached != gpu_lines:
        return {}
    return {key: block.result() for key, block in blocks.items()}
