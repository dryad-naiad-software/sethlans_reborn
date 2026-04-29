# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cross-process loopback_port hand-off contract (issues #180 + #181).

The launcher's wizard hand-off depends on three things lining up:

1. Both the launcher and the wizard subprocess resolve the SAME shared
   data directory. Pre-#181 the launcher's ``get_data_dir`` ignored
   ``SETHLANS_DATA_DIR``, so the wizard wrote ``loopback_port`` under
   ``temp/dev-data/wizard/`` while the launcher polled ``%LOCALAPPDATA%
   \\Sethlans\\wizard\\``. The poll always timed out (#180).
2. The wizard subprocess writes its plain-HTTP loopback port to
   ``<data_dir>/wizard/loopback_port`` (FR-W3 / issue #170).
3. The launcher's :func:`launcher.wizard_caddy_lifecycle.
   wait_for_wizard_loopback_port` resolves that file before the 10 s
   timeout.

This test exercises the whole path: spawn the real wizard subprocess
with ``SETHLANS_DATA_DIR`` set, then run the launcher's poll helper
and assert it returns the same port the wizard bound. Catches
regressions in either direction of the contract (writer rename, polled
path drift, env-var resolution mismatch).
"""

from __future__ import annotations

from launcher.wizard_caddy_lifecycle import wait_for_wizard_loopback_port


def test_launcher_resolves_wizard_loopback_port(wizard_process):
    """Launcher's poll helper resolves the wizard's loopback_port file.

    The :data:`wizard_process` fixture (see ``conftest.py``) already
    sets ``SETHLANS_DATA_DIR=<tmp>/sethlans`` on the wizard subprocess
    and waits for the HTTP listener to become ready. By the time we
    run, ``<data_dir>/wizard/loopback_port`` exists with the bound port
    inside.
    """
    resolved = wait_for_wizard_loopback_port(
        wizard_process.data_dir,
        wizard_process.proc,
        timeout=5.0,
        poll_interval=0.1,
    )
    assert resolved is not None, (
        "launcher poll returned None — port file missing or unreadable "
        f"at {wizard_process.wizard_subdir / 'loopback_port'}"
    )
    assert resolved == wizard_process.port, (
        f"launcher resolved {resolved}; wizard bound "
        f"{wizard_process.port}"
    )


def test_launcher_resolves_wizard_loopback_port_under_dev_data_dir_env(
    wizard_process,
):
    """Issue #181 + #180: ``SETHLANS_DATA_DIR`` is the shared contract.

    The fixture set ``SETHLANS_DATA_DIR`` on the wizard subprocess; the
    launcher's :func:`launcher.paths.get_data_dir` honors the same env
    var (post-#181) so a developer running ``tools/dev_launcher.py``
    sees both processes agree on the shared root. We assert the
    fixture's data dir matches what the launcher would have resolved
    if it ran in the same env.
    """
    # Read the env var the fixture set; we don't actually mutate this
    # process's env (the fixture's subprocess already has its own env
    # snapshot at spawn time).
    import os

    from launcher.paths import get_data_dir as launcher_get_data_dir

    # The fixture's subprocess was launched with SETHLANS_DATA_DIR
    # pointing at wizard_process.data_dir. To prove the launcher would
    # resolve the same path, set it temporarily in this process and
    # call the resolver.
    prior = os.environ.get("SETHLANS_DATA_DIR")
    os.environ["SETHLANS_DATA_DIR"] = str(wizard_process.data_dir)
    try:
        resolved = launcher_get_data_dir()
    finally:
        if prior is None:
            os.environ.pop("SETHLANS_DATA_DIR", None)
        else:
            os.environ["SETHLANS_DATA_DIR"] = prior
    assert resolved == wizard_process.data_dir, (
        f"launcher resolved {resolved}; wizard used "
        f"{wizard_process.data_dir}"
    )
