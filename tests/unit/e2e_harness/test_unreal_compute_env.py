# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The Unreal E2E worker's card choice (``tests/e2e/unreal_compute_env.py``).

A worker with no card choice is CPU-only and never claims an Unreal job,
so the harness sets ``cpu_gpu`` and the probed card addresses, keeps a
caller's ``SETHLANS_COMPUTE_CARDS``, and skips when no card is found.
"""

from sethlans_worker_agent.cards.identity import parse_key_list
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.e2e.unreal_compute_env import (
    CARDS_ENV,
    METHOD_ENV,
    compute_env,
    probed_addresses,
)


def _probe_of(*keys):
    return lambda: tuple(OsCard(key=key, name=f"card {i}") for i, key in enumerate(keys))


def _failing_probe():
    return None


def _unused_probe():
    raise AssertionError("the probe must not run when the caller set the cards")


def test_probed_cards_become_a_valid_card_list():
    updates, skip = compute_env({}, _probe_of("pci:0000:01:00.0", "pci:0000:2d:00.0"))
    assert skip is None
    assert updates == {
        METHOD_ENV: "cpu_gpu",
        CARDS_ENV: "0000:01:00.0,0000:2d:00.0",
    }
    # The worker's own parser accepts the value as two card keys.
    assert parse_key_list(updates[CARDS_ENV], 16) == ("pci:0000:01:00.0", "pci:0000:2d:00.0")


def test_caller_cards_are_kept_and_the_probe_not_run():
    updates, skip = compute_env({CARDS_ENV: "0000:65:00.0"}, _unused_probe)
    assert skip is None
    assert updates == {METHOD_ENV: "cpu_gpu"}


def test_blank_caller_cards_fall_back_to_the_probe():
    updates, skip = compute_env({CARDS_ENV: "  "}, _probe_of("pci:0000:01:00.0"))
    assert skip is None
    assert updates[CARDS_ENV] == "0000:01:00.0"


def test_failed_probe_skips_with_a_reason():
    updates, skip = compute_env({}, _failing_probe)
    assert updates is None
    assert "failed" in skip and CARDS_ENV in skip


def test_no_card_skips_with_a_reason():
    updates, skip = compute_env({}, _probe_of())
    assert updates is None
    assert "no graphics card" in skip and CARDS_ENV in skip


def test_cards_without_a_pci_key_are_left_out():
    assert probed_addresses(_probe_of(None, "pci:0000:03:00.0")) == ("0000:03:00.0",)
    updates, skip = compute_env({}, _probe_of(None))
    assert updates is None
    assert "no graphics card" in skip
