# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for uvicorn event loop factory (GitHub #77)."""

from __future__ import annotations

import asyncio

from sethlans_manager.event_loop_factory import new_selector_event_loop


def test_factory_returns_selector_event_loop():
    loop = new_selector_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_factory_returns_fresh_instances():
    a = new_selector_event_loop()
    b = new_selector_event_loop()
    try:
        assert a is not b
    finally:
        a.close()
        b.close()


def test_returned_loop_is_runnable():
    loop = new_selector_event_loop()
    try:
        async def _identity():
            return 42
        result = loop.run_until_complete(_identity())
        assert result == 42
    finally:
        loop.close()
