# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Graphics card inventory, identity and (later) the person's card choice.

The operating system lists the cards (``os_probe``); Blender's device list is
joined to it by PCI address (``inventory``). A card is identified by its card
key (``identity``): ``pci:<dddd:bb:dd.f>`` on Windows and Linux, ``apple-gpu``
on macOS. Windows-only code is imported inside functions.
"""
