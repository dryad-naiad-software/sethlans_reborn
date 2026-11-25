# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: the process sweep kills only this checkout's processes.

sethlans.sh: the Git Bash sweep lists processes with ``powershell.exe`` and
kills them with ``taskkill``. A fake ``powershell.exe`` first on PATH ignores
its arguments and prints ``Name<TAB>PID<TAB>CommandLine`` lines; a fake
``taskkill`` logs the PIDs it is given; nothing real is listed or killed. A
fake ``uname`` selects the Git Bash branch, and off Windows a fake ``cygpath``
stands in for Git Bash's, so the same branch runs there too.

sethlans.ps1: ``Test-OurPythonCmd`` / ``Test-OurCaddyCmd`` (with
``ConvertTo-CmdKey``, ``Test-ArgStartsWith`` and the ``$RootKey``
assignment) are taken from the script's syntax tree and run on their own, so
the script's dispatch never runs.

A path matches only where it starts an argument: start of the line, or after
a space or a quote.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from . import _clean_runner as runner
from ._clean_asserts import output
from ._clean_runner import GIT_BASH_UNAME, PS_SHELLS
from .conftest import IS_WINDOWS, SCRIPT_TIMEOUT, clean_env, run_clean

_MATCHER_HARNESS = r"""
param([string]$ScriptPath, [string]$Root, [string]$CasesFile)
$ErrorActionPreference = "Stop"
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "parse errors: $errors" }
$names = @('ConvertTo-CmdKey', 'Test-ArgStartsWith', 'Test-OurPythonCmd', 'Test-OurCaddyCmd')
$funcs = @($ast.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $n.Name }, $false))
if ($funcs.Count -ne $names.Count) { throw "expected the $($names.Count) matcher functions, found $($funcs.Count)" }
$assign = @($ast.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$RootKey' }, $false))
if ($assign.Count -ne 1) { throw "expected one `$RootKey assignment, found $($assign.Count)" }
$ProjectRoot = $Root
foreach ($f in $funcs) { . ([scriptblock]::Create($f.Extent.Text)) }
. ([scriptblock]::Create($assign[0].Extent.Text))
# Windows PowerShell 5.1 pipes a JSON array on as one object: assign it first.
$cases = Get-Content -Raw -LiteralPath $CasesFile | ConvertFrom-Json
foreach ($c in $cases) {
    if ($c.kind -eq 'python') { $r = Test-OurPythonCmd $c.line } else { $r = Test-OurCaddyCmd $c.line }
    Write-Output ([bool]$r)
}
"""


def _forms(root, rel):
    """``root/rel`` with '/', with '\\', and in mixed case with mixed separators."""
    back = root.replace("/", "\\")
    return [
        f"{root}/{rel}",
        back + "\\" + rel.replace("/", "\\"),
        root.swapcase() + "\\" + rel.upper().replace("/", "\\", 1),
    ]


def _anchor_cases(root):
    """The left anchor: quotes and line start match; a path ending in the root does not."""
    py = '"C:\\Python314\\python.exe" '
    back = root.replace("/", "\\")
    return [
        ("python", py + '"' + back + '\\manager\\run_manager.py" --flag', True),
        ("python", py + "'" + root + "/worker/run_worker.py'", True),
        ("python", root + "/manager/run_manager.py", True),
        ("caddy", 'caddy.exe run --config "' + back + '\\manager\\caddy\\Caddyfile"', True),
        ("python", py + "/x" + root + "/manager/run_manager.py", False),
        ("python", py + "C:\\nest" + back + "\\worker\\run_worker.py", False),
        ("python", py + "--x=" + root + "/manager/run_manager.py", False),
        ("python", py + "--x=" + root + "/manager/manage.py runserver", False),
        ("caddy", "caddy.exe run --config=" + root + "/manager/caddy/Caddyfile", False),
        ("caddy", "caddy.exe run --config /x" + root + "/manager/caddy/Caddyfile", False),
    ]


def sample_cases(root):
    """(kind, command line, should match) for this checkout at ``root`` ('/' form)."""
    py = '"C:\\Python314\\python.exe" '
    caddy = "caddy.exe run --adapter caddyfile --config "
    cases = []
    for rel in ("manager/run_manager.py", "worker/run_worker.py"):
        cases += [("python", py + path + " --flag", True) for path in _forms(root, rel)]
    cases += [("python", py + path + " RunServer 127.0.0.1:8000", True)
              for path in _forms(root, "manager/manage.py")]
    cases += [("caddy", caddy + path, True) for path in _forms(root, "manager/caddy/Caddyfile")]
    sibling = root + "-wt"
    cases += [
        ("python", py + f"{sibling}/manager/run_manager.py", False),
        ("python", py + sibling.replace("/", "\\") + "\\worker\\run_worker.py", False),
        ("python", py + f"{sibling}/manager/manage.py runserver", False),
        ("python", py + f"{root}/manager/manage.py migrate", False),
        ("python", py + root.replace("/", "\\") + "\\manager\\manage.py migrate --noinput", False),
        ("python", py + "C:/elsewhere/manager/run_manager.py", False),
        ("python", "", False),
        ("caddy", caddy + "C:/elsewhere/manager/caddy/Caddyfile", False),
        ("caddy", caddy + f"{sibling}/manager/caddy/Caddyfile", False),
        ("caddy", caddy + f"{root}/other/Caddyfile", False),
        ("caddy", "", False),
    ]
    return cases + _anchor_cases(root)


_IMAGE_NAMES = {"python": ("python.exe", "Python.EXE", "PYTHON.exe"), "caddy": ("caddy.exe", "Caddy.EXE")}


def _process_lines(cases):
    """Fake powershell.exe output for ``cases``: (bytes, sorted PIDs that must be killed)."""
    out, expected = [], []
    for offset, (kind, line, match) in enumerate(cases):
        pid = (1000 if kind == "python" else 5000) + offset
        name = _IMAGE_NAMES[kind][offset % len(_IMAGE_NAMES[kind])]
        end = "\r\n" if offset % 2 else "\n"
        out.append(f"{name}\t{pid}\t{line}{end}")
        if match:
            expected.append(pid)
    return "".join(out).encode(), sorted(expected)


def _skipped_lines(root):
    """Lines the sweep must skip although the command line is this checkout's."""
    ours = f'"C:\\Python314\\python.exe" {root}/manager/run_manager.py'
    return (
        f"notepad.exe\t9001\t{ours}\n"
        f"pythonw.exe\t9002\t{ours}\n"
        f"python.exe\t12a\t{ours}\n"
        f"python.exe\t\t{ours}\n"
        "python.exe\t9005\t\n"
        f"caddy.exe\t-1\tcaddy.exe run --config {root}/manager/caddy/Caddyfile\n"
    ).encode()


def _prepare_sweep(project):
    runner.require_shell("sh")
    if not IS_WINDOWS and not (shutil.which("iconv") and shutil.which("base64")):
        pytest.skip("the Git Bash sweep needs iconv and base64")
    runner.install_fake_cygpath(project)
    runner.install_fake_uname(project, GIT_BASH_UNAME)
    return _script_root(project)


def _script_root(project_root):
    """The project root as sethlans.sh compares it: ``cygpath -m "$(realpath ...)"``."""
    command = 'r=$(realpath "$1") && cygpath -m "$r"'
    env = dict(os.environ)
    env["PATH"] = runner.child_path("sh", project_root, env)
    out = subprocess.run([runner.require_shell("sh"), "-c", command, "root", str(project_root)],
                         env=env, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, check=True)
    return out.stdout.strip().rstrip("/")


def _killed(log: Path):
    return sorted(int(line.split()[-1]) for line in log.read_text().splitlines()) if log.exists() else []


def _run_sweep(project, processes):
    log = runner.install_fake_sweep(project, processes)
    result = run_clean(project, None, shell="sh", uname=GIT_BASH_UNAME)
    assert result.returncode == 0, output(result)
    return result, log


class TestShWindowsSweep:
    def test_kills_only_this_checkouts_processes(self, fake_project):
        root = _prepare_sweep(fake_project)
        processes, expected = _process_lines(sample_cases(root))

        result, log = _run_sweep(fake_project, processes)

        assert _killed(log) == expected, output(result)
        for pid in expected:
            assert f"[KILL] Taskkill {'python' if pid < 5000 else 'caddy'} PID {pid}" in result.stdout

    def test_skips_other_images_bad_pids_and_empty_command_lines(self, fake_project):
        root = _prepare_sweep(fake_project)

        result, log = _run_sweep(fake_project, _skipped_lines(root))

        assert _killed(log) == [], output(result)
        assert "[KILL]" not in result.stdout

    def test_taskkill_gets_force_and_pid_flags(self, fake_project):
        root = _prepare_sweep(fake_project)

        _, log = _run_sweep(fake_project, f"python.exe\t42\tpython.exe {root}/worker/run_worker.py\r\n".encode())

        assert log.read_text().splitlines() == ["//F //PID 42"]

    def test_no_processes_kills_nothing(self, fake_project):
        _prepare_sweep(fake_project)

        result, log = _run_sweep(fake_project, b"")

        assert not log.exists()
        assert "[KILL]" not in result.stdout

    def test_without_powershell_the_sweep_is_a_silent_no_op(self, fake_project):
        _prepare_sweep(fake_project)
        log = runner.install_fake_sweep(fake_project, with_powershell=False)
        env = clean_env(fake_project)
        env["PATH"] = os.pathsep.join(
            d for d in env["PATH"].split(os.pathsep) if d and not (Path(d) / "powershell.exe").exists())
        probe_env = dict(env, PATH=runner.child_path("sh", fake_project, env))
        found = subprocess.run([runner.require_shell("sh"), "-c", "command -v powershell.exe || true"],
                               env=probe_env, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
        assert found.stdout.strip() == "", "precondition: no powershell.exe on the child's PATH"

        result = run_clean(fake_project, env, shell="sh", uname=GIT_BASH_UNAME)

        assert result.returncode == 0, output(result)
        assert "[OK] Clean complete." in result.stdout
        assert "[KILL]" not in result.stdout
        assert "powershell" not in result.stderr.lower(), output(result)
        assert not log.exists()


@pytest.mark.parametrize("shell", PS_SHELLS)
def test_ps1_matchers_pick_only_this_checkout(fake_project, shell):
    exe = runner.require_shell(shell)
    root = str(fake_project)
    cases = sample_cases(root.replace("\\", "/"))
    cases_file = fake_project / "matcher-cases.json"
    cases_file.write_text(json.dumps([{"kind": k, "line": line} for k, line, _ in cases]))
    harness = fake_project / "matcher-harness.ps1"
    harness.write_text(_MATCHER_HARNESS)

    out = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
         "-ScriptPath", str(fake_project / "tools" / "sethlans.ps1"), "-Root", root, "-CasesFile", str(cases_file)],
        capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
    )

    assert out.returncode == 0, f"stdout:\n{out.stdout}\nstderr:\n{out.stderr}"
    got = [line.strip() == "True" for line in out.stdout.splitlines() if line.strip()]
    assert len(got) == len(cases), out.stdout
    mismatches = [(kind, line) for (kind, line, want), have in zip(cases, got) if want != have]
    assert mismatches == []
