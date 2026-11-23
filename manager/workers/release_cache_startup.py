# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Start of the background Blender series fetch (issue #292).

The fetch (:func:`workers.utils.blender_series_cache.populate_cache`,
which contacts download.blender.org) runs only in a process that serves
requests:

* the Waitress serving process — ``sethlans_manager.waitress_launcher.launch``
  calls :func:`start_release_cache_populate` (source and frozen builds,
  the Docker images and the ``--dev`` child all serve through it);
* Django's ``runserver`` in development — ``WorkersConfig.ready()`` calls
  :func:`start_if_runserver`, which starts the fetch only in the process
  that serves: the autoreloader's child, or the single process under
  ``--noreload``.

``AppConfig.ready()`` also runs for every management command
(``migrate``, ``spectacular``, ``check``, ...), for the autoreloader's
parent and for the migrate / collectstatic subprocesses that
``run_manager.py`` starts; none of those fetch.  Under pytest, or with
``SETHLANS_DISABLE_RELEASE_FETCH=1``, nothing fetches at all.
"""

import os
import sys
import threading

THREAD_NAME = 'blender-series-cache-populate'
DISABLE_ENV = 'SETHLANS_DISABLE_RELEASE_FETCH'

_lock = threading.Lock()
_started = False


def fetch_disabled() -> bool:
    """True when the operator opted out or the process runs under pytest.

    Only the literal ``'1'`` opts out.  Tests that need the cache fill
    it through their own fixtures or mocks.
    """
    if os.environ.get(DISABLE_ENV) == '1':
        return True
    return 'pytest' in sys.modules


def is_runserver_serving_process(argv=None, environ=None) -> bool:
    """True in the process of ``manage.py runserver`` that serves requests.

    Django takes the subcommand from ``argv[1]``.  With the autoreloader
    the parent only watches files and restarts its child; Django marks
    the child with ``RUN_MAIN=true`` (``DJANGO_AUTORELOAD_ENV``).  Under
    ``--noreload`` the one process serves.  Requiring ``runserver`` as
    well keeps a ``RUN_MAIN`` inherited by some other command closed.
    """
    from django.utils.autoreload import DJANGO_AUTORELOAD_ENV

    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    if list(argv[1:2]) != ['runserver']:
        return False
    if environ.get(DJANGO_AUTORELOAD_ENV) == 'true':
        return True
    return '--noreload' in argv[2:]


def start_release_cache_populate() -> bool:
    """Start the fetch thread once per process; returns True if it started.

    Called by the serving process only.  A second call in the same
    process is a no-op, and so is any call under pytest or with the
    opt-out set.
    """
    global _started
    if fetch_disabled():
        return False
    with _lock:
        if _started:
            return False
        _started = True
    from .utils.blender_series_cache import populate_cache
    threading.Thread(
        target=populate_cache, name=THREAD_NAME, daemon=True,
    ).start()
    return True


def start_if_runserver() -> bool:
    """``AppConfig.ready()`` hook: start the fetch in runserver's serving process only."""
    if not is_runserver_serving_process():
        return False
    return start_release_cache_populate()
