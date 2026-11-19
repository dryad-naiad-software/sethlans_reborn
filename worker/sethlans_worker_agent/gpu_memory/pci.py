# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
PCI address normalisation.

``normalize_pci`` turns the address forms the worker meets into one text
form, ``dddd:bb:dd.f`` in lower-case hex:

- ``dddd:bb:dd.f`` from Linux sysfs, with a domain of 4 to 8 hex digits
  (a Linux VMD domain such as ``10000`` has five);
- ``dddddddd:bb:dd.f`` from ``nvidia-smi``;
- the D3DKMT triple ``(bus, device, function)`` from Windows, domain 0.

A domain above ``0xffff`` keeps its digits. Anything else, including a bus
above ``0xff``, a device above ``0x1f`` or a function above ``7`` (the
all-ones address of the software adapter), is ``None``.
"""
import re
from typing import Optional

_TEXT = re.compile(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])')

MAX_BUS = 0xFF
MAX_DEVICE = 0x1F
MAX_FUNCTION = 0x7


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def format_pci(domain: int, bus: int, device: int, function: int) -> Optional[str]:
    """``dddd:bb:dd.f`` for integer parts in range, else ``None``."""
    if not all(_is_int(part) for part in (domain, bus, device, function)):
        return None
    if not (0 <= domain <= 0xFFFFFFFF and 0 <= bus <= MAX_BUS
            and 0 <= device <= MAX_DEVICE and 0 <= function <= MAX_FUNCTION):
        return None
    return f'{domain:04x}:{bus:02x}:{device:02x}.{function:x}'


def normalize_pci(value) -> Optional[str]:
    """Normalise a PCI address text or a D3DKMT ``(bus, device, function)`` triple."""
    if isinstance(value, tuple):
        if len(value) != 3:
            return None
        return format_pci(0, *value)
    if not isinstance(value, str):
        return None
    match = _TEXT.fullmatch(value)
    if match is None:
        return None
    domain, bus, device, function = (int(part, 16) for part in match.groups())
    return format_pci(domain, bus, device, function)
