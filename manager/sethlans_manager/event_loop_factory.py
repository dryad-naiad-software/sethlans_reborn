# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Event loop factory for uvicorn's ``--loop`` parameter.

Passed to uvicorn as the dotted path ``sethlans_manager.event_loop_factory
:new_selector_event_loop``.  Uvicorn calls the function once per worker to
obtain a fresh event loop instance.

Only used on Windows to avoid the ``WindowsProactorEventLoop`` socket
leak (GitHub #77).  ``asyncio.SelectorEventLoop`` is a concrete class
and is NOT part of the deprecated-in-3.14 policy system, so this code
survives the asyncio simplification slated for Python 3.16.
"""

from __future__ import annotations

import asyncio


def new_selector_event_loop() -> asyncio.AbstractEventLoop:
    """Return a fresh ``SelectorEventLoop`` instance.

    Uvicorn invokes this once per worker; it must return a new loop
    object each call — never a cached singleton.
    """
    return asyncio.SelectorEventLoop()
