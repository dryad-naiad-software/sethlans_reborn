# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Guard for issue #277: no database test runs after the first Playwright test.

Playwright's sync API leaves an asyncio loop running in the main thread from
its first test to the end of the session, and Django then refuses ORM calls
in that thread (``SynchronousOnlyOperation``). pytest-django runs database
tests first and other tests last, which keeps the Playwright tests at the end,
but a Playwright test that lists ``db`` or ``transactional_db`` in its
signature is sorted with the database tests, and pytest then pulls every
Playwright test (one ``browser_name`` group) ahead of the remaining database
tests. A browser test that needs the database therefore starts and stops its
own Playwright (``_manager_worker_list_browser.own_chromium``). This test
checks the collected order of the whole session.
"""

from django.test import TestCase, TransactionTestCase

PLAYWRIGHT_FIXTURES = {'playwright', 'browser', 'context', 'page', 'browser_name'}


def _uses_database(item):
    """True when pytest-django sorts ``item`` with the database tests (its own rule)."""
    test_cls = getattr(item, 'cls', None)
    if test_cls is not None and issubclass(test_cls, (TestCase, TransactionTestCase)):
        return True
    fixtures = set(getattr(item, 'fixturenames', ()))
    return item.get_closest_marker('django_db') is not None or bool(fixtures & {'db', 'transactional_db'})


def _uses_playwright(item):
    return bool(set(getattr(item, 'fixturenames', ())) & PLAYWRIGHT_FIXTURES)


def test_no_database_test_runs_after_a_playwright_test(request):
    items = request.session.items
    first = next((index for index, item in enumerate(items) if _uses_playwright(item)), None)
    if first is None:
        return
    late = [item.nodeid for item in items[first:] if _uses_database(item)]
    assert late == [], f'database tests run after the first Playwright test ({items[first].nodeid}): {late[:10]}'
