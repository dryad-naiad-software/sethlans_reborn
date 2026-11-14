# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Windows smoke tests for the session-monitor message window (issue #234).

These hit the real Win32 API. Before the fix, ``CreateWindowExW`` raised
``OverflowError: int too long to convert`` on the 64-bit ``hInstance`` and
the session-monitor thread died at every worker start.

Each test runs the Win32 work on its own thread (as production does) with a
short timeout. Exceptions are captured inside the thread so a failure shows
up as an assertion, never as an unhandled thread exception.
"""
import logging
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows-only Win32 smoke test')

MODULE = 'sethlans_worker_agent.idle_detection.session_win32'
TIMEOUT = 10.0


def _run_in_thread(target):
    """Run ``target`` on a daemon thread; return (finished, captured exception)."""
    errors = []

    def runner():
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 - report every failure as an assertion
            errors.append(exc)

    thread = threading.Thread(target=runner, name='session-monitor-test', daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    return not thread.is_alive(), errors


def test_create_and_destroy_message_window():
    """The message-only window is created and destroyed without raising."""
    from sethlans_worker_agent.idle_detection import session_win32, win32_api as api

    seen = {}

    def work():
        callback = session_win32._make_wnd_proc(api)
        hwnd, hinstance = session_win32._create_message_window(api, callback)
        seen['hwnd'], seen['hinstance'] = hwnd, hinstance
        seen['alive_before'] = bool(api.user32.IsWindow(hwnd))
        session_win32._destroy_message_window(api, hwnd, hinstance)
        seen['alive_after'] = bool(api.user32.IsWindow(hwnd))

    finished, errors = _run_in_thread(work)

    assert finished, 'window create/destroy did not finish in time'
    assert errors == []
    assert seen['hwnd'] and seen['hinstance']
    assert seen['alive_before'] is True
    assert seen['alive_after'] is False


def test_create_and_destroy_twice():
    """Cleanup unregisters the class, so a second window can be made."""
    test_create_and_destroy_message_window()
    test_create_and_destroy_message_window()


def test_message_pump_runs_and_stops_cleanly(caplog):
    """The full pump starts, registers, and exits when its window closes."""
    from sethlans_worker_agent.idle_detection import session_win32, win32_api as api

    ready = threading.Event()
    box = {}

    def on_ready(hwnd):
        box['hwnd'] = hwnd
        ready.set()

    caplog.set_level(logging.DEBUG, logger=MODULE)
    errors = []

    def runner():
        try:
            session_win32._win32_message_pump(on_ready=on_ready)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=runner, name='session-monitor-test', daemon=True)
    thread.start()
    try:
        assert ready.wait(TIMEOUT), 'session monitor window never became ready'
        assert api.user32.PostMessageW(box['hwnd'], session_win32.WM_CLOSE, 0, 0)
    finally:
        thread.join(TIMEOUT)

    assert not thread.is_alive(), 'message pump did not exit after WM_CLOSE'
    assert errors == []
    messages = [r.getMessage() for r in caplog.records if r.name == MODULE]
    assert 'Session monitor thread failed.' not in messages
    assert 'Windows session monitor stopped.' in messages
    assert not api.user32.IsWindow(box['hwnd'])


def test_window_procedure_sets_unlock_event():
    """A WM_WTSSESSION_CHANGE / unlock dispatched to the window sets the event."""
    from sethlans_worker_agent.idle_detection import session_win32, win32_api as api

    session_win32.session_unlock_event.clear()
    seen = {}

    def work():
        callback = session_win32._make_wnd_proc(api)
        hwnd, hinstance = session_win32._create_message_window(api, callback)
        try:
            # Large LPARAM exercises the pointer-sized parameter path too.
            seen['result'] = callback(
                hwnd, session_win32.WM_WTSSESSION_CHANGE,
                session_win32.WTS_SESSION_UNLOCK, 0x7FFF_FFFF_FFFF,
            )
        finally:
            session_win32._destroy_message_window(api, hwnd, hinstance)

    try:
        finished, errors = _run_in_thread(work)
        assert finished and errors == []
        assert session_win32.session_unlock_event.is_set()
    finally:
        session_win32.session_unlock_event.clear()
