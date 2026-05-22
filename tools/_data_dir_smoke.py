# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #195 data-dir alignment smoke check for ``tools/wizard_smoke.py``.

Split out of ``tools/_wizard_smoke_helpers.py`` to keep that file under
the 300-line project cap (CLAUDE.md). Owns the harness that exercises
:func:`launcher.apply_pending_setup.run_apply_pipeline_if_needed` and
asserts the ``apply_pending_setup`` subprocess receives ``--data-dir
<shared_root>`` (NOT ``<shared_root>/manager``).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys


def err(msg: str) -> None:
    """stderr print shortcut.

    Inlined (rather than imported from :mod:`_wizard_smoke_helpers`)
    so this module imports cleanly without a sibling ``tools/`` entry
    on ``sys.path`` — mirrors the :mod:`_resolver_smoke` pattern.
    """
    print(msg, file=sys.stderr)


def _harness_source() -> str:
    """Return the Python source executed by :func:`check_data_dir_alignment`.

    Issue #195: the launcher's ``run_apply_pipeline_if_needed`` used to
    append ``/manager`` to ``data_dir`` before passing it to
    ``apply_pending_setup --data-dir``, but the wizard writes
    ``pending_setup.json`` to the shared root. The mismatch broke every
    manager-bearing topology with ``apply pre-guard failed:
    pending_setup.json missing``.

    The harness:

    1. creates a fresh shared-root tmp dir, drops a fake
       ``pending_setup.json`` there;
    2. monkey-patches :func:`subprocess.run` to capture each ``argv``
       without spawning a real process (returning a fake completed
       process with rc=0 so the pipeline reports success);
    3. invokes ``run_apply_pipeline_if_needed(topology='manager_worker',
       data_dir=shared_root, ...)``;
    4. asserts the captured ``apply_pending_setup`` argv contains
       ``--data-dir <shared_root>`` and NOT ``--data-dir
       <shared_root>/manager``.

    The pre-#195 code would have failed step 4 because the launcher
    appended ``/manager`` before invoking the subprocess.
    """
    return (
        "import os, sys, subprocess, json, tempfile, pathlib\n"
        "shared_root = pathlib.Path(tempfile.mkdtemp(\n"
        "    prefix='sethlans-datadir-smoke-')).resolve()\n"
        "(shared_root / 'pending_setup.json').write_text(\n"
        "    json.dumps({'version': 1}), encoding='utf-8')\n"
        "captured = []\n"
        "real_run = subprocess.run\n"
        "def fake_run(cmd, *a, **kw):\n"
        "    captured.append(list(cmd))\n"
        "    class R:\n"
        "        returncode = 0\n"
        "        stdout = ''\n"
        "        stderr = ''\n"
        "    return R()\n"
        "subprocess.run = fake_run\n"
        "try:\n"
        "    from launcher import apply_pending_setup as ap\n"
        "    ap.subprocess.run = fake_run\n"
        "    rc = ap.run_apply_pipeline_if_needed(\n"
        "        topology='manager_worker',\n"
        "        data_dir=shared_root,\n"
        "        wizard_proc=None,\n"
        "        terminate_wizard_cb=lambda p: None,\n"
        "        failure_exit_cb=lambda reason: 99,\n"
        "    )\nfinally:\n"
        "    subprocess.run = real_run\n"
        "if rc is not None:\n"
        "    print('FAIL pipeline returned non-None:', rc, file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "apply_argv = None\n"
        "for cmd in captured:\n"
        "    if 'apply_pending_setup' in cmd:\n"
        "        apply_argv = cmd\n"
        "        break\n"
        "if apply_argv is None:\n"
        "    print('FAIL no apply_pending_setup argv captured; got:',\n"
        "          captured, file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "if '--data-dir' not in apply_argv:\n"
        "    print('FAIL --data-dir missing from argv:', apply_argv,\n"
        "          file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "idx = apply_argv.index('--data-dir')\n"
        "if idx + 1 >= len(apply_argv):\n"
        "    print('FAIL --data-dir without value:', apply_argv,\n"
        "          file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "actual = pathlib.Path(apply_argv[idx + 1]).resolve()\n"
        "wrong = (shared_root / 'manager').resolve()\n"
        "if actual == wrong:\n"
        "    print('FAIL #195 regression: --data-dir is',\n"
        "          actual, '(legacy /manager append)',\n"
        "          file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "if actual != shared_root:\n"
        "    print('FAIL --data-dir mismatch: got', actual,\n"
        "          'expected', shared_root, file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "print('OK --data-dir', actual)\n"
    )


def check_data_dir_alignment() -> bool:
    """Issue #195: assert ``run_apply_pipeline_if_needed`` passes shared root.

    Build-time guard that the launcher invokes ``apply_pending_setup
    --data-dir <shared_root>`` (where the wizard wrote
    ``pending_setup.json``), not ``--data-dir <shared_root>/manager``.

    Runs the harness from :func:`_harness_source` in a fresh Python
    subprocess so the import of :mod:`launcher.apply_pending_setup` is
    clean (no fixture leakage, no monkey-patching that could mask a
    regression). Pre-fix, the launcher would have produced ``--data-dir
    <shared_root>/manager`` and the harness would exit non-zero on the
    mismatch check.

    Pure-Python; no bundle dependency — runs against the source tree
    via PYTHONPATH so it works in wizard-only iterations too. Wired
    into :mod:`tools.wizard_smoke` behind ``--skip-data-dir-smoke``.
    """
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp
        else str(repo_root)
    )
    print("#195 invoking data-dir alignment harness")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _harness_source()],
            capture_output=True, timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        err("#195 FAILED: data-dir alignment harness timed out")
        return False
    if result.returncode != 0:
        err(
            f"#195 FAILED: harness exit={result.returncode}\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {result.stderr!r}"
        )
        return False
    printed = result.stdout.decode("utf-8", errors="replace").strip()
    print(f"#195 passed: {printed}")
    return True
