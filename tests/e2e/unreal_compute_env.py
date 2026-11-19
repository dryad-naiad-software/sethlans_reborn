# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The card choice for the Unreal E2E worker.

A worker with no saved card choice renders on the CPU only and has no GPU
slot, so it never claims an Unreal job. ``compute_env`` gives the worker
``SETHLANS_COMPUTE_METHOD=cpu_gpu`` and, unless the caller already set
``SETHLANS_COMPUTE_CARDS``, the PCI addresses of the cards the worker's
own OS probe lists. With no card to give, it returns a skip reason so the
test stops at once instead of waiting for a claim that never comes.
"""

import os

METHOD_ENV = "SETHLANS_COMPUTE_METHOD"
CARDS_ENV = "SETHLANS_COMPUTE_CARDS"
_KEY_PREFIX = "pci:"


def _probe():
    # Imported lazily: only an opted-in node gets this far.
    from sethlans_worker_agent.cards import os_probe
    return os_probe.probe()


def probed_addresses(probe=_probe):
    """PCI addresses of the probed cards, ``()`` for none, ``None`` when the probe failed."""
    cards = probe()
    if cards is None:
        return None
    return tuple(
        card.key[len(_KEY_PREFIX):] for card in cards
        if isinstance(card.key, str) and card.key.startswith(_KEY_PREFIX)
    )


def compute_env(environ=None, probe=_probe):
    """``(env_updates, skip_reason)``; exactly one of the two is ``None``."""
    environ = os.environ if environ is None else environ
    updates = {METHOD_ENV: "cpu_gpu"}
    if environ.get(CARDS_ENV, "").strip():
        return updates, None  # the caller's choice is kept as it is
    addresses = probed_addresses(probe)
    if addresses is None:
        return None, (
            f"listing the graphics cards through the operating system failed; "
            f"set {CARDS_ENV} to the card PCI addresses"
        )
    if not addresses:
        return None, (
            f"the operating system lists no graphics card with a PCI address; "
            f"set {CARDS_ENV} to the card PCI addresses"
        )
    updates[CARDS_ENV] = ",".join(addresses)
    return updates, None
