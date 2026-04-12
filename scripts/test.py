# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Test runner for Sethlans Reborn.

Runs pytest tiers, flake8, `manage.py check`, and
`manage.py makemigrations --dry-run --check` via a single entry point.

Commands (individual steps):
    unit          Run unit tests (tests/unit/)
    integration   Run integration tests (tests/integration/)
    e2e           Run e2e tests (tests/e2e/)
    lint          Run flake8 across the repo
    check         Run manage.py check
    migrations    Run manage.py makemigrations --dry-run --check

Commands (presets):
    fast          unit + lint (quick dev feedback)
    backend       unit + integration + lint + check + migrations
    full          everything including e2e

Examples:
    python scripts/test.py unit
    python scripts/test.py unit --path tests/unit/worker/test_wizard.py
    python scripts/test.py fast -x
    python scripts/test.py full --verbose
    python scripts/test.py backend --no-migrations
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANAGER_DIR = PROJECT_ROOT / "manager"
MANAGE_PY = MANAGER_DIR / "manage.py"

# Flake8 config
FLAKE8_ARGS = ["--max-line-length", "127", "--max-complexity", "10"]

# Django management env prefix (pytest.ini already sets this for pytest runs,
# so we only need it for direct manage.py invocations).
DJANGO_ENV_OVERRIDES = {"SETHLANS_SECURITY_DEBUG": "True"}


def find_venv_binary(name: str) -> Path:
    """Locate a binary inside the project venv (Windows or POSIX)."""
    win = PROJECT_ROOT / ".venv" / "Scripts" / f"{name}.exe"
    if win.exists():
        return win
    posix = PROJECT_ROOT / ".venv" / "bin" / name
    if posix.exists():
        return posix
    raise FileNotFoundError(
        f"Could not find {name} in .venv/. Is the virtualenv installed?"
    )


VENV_PYTHON = find_venv_binary("python")
VENV_FLAKE8 = find_venv_binary("flake8")


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------


def _banner(name: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n== {name}\n{bar}", flush=True)


def _run(cmd: list, env_overrides: dict = None, cwd: Path = None) -> bool:
    """Run a subprocess. Returns True on exit 0."""
    env = None
    if env_overrides:
        env = os.environ.copy()
        env.update(env_overrides)
    printable = " ".join(str(c) for c in cmd)
    print(f"$ {printable}", flush=True)
    try:
        result = subprocess.run(cmd, env=env, cwd=cwd)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return False
    return result.returncode == 0


def step_pytest(tier_dir: str, args) -> bool:
    """Run pytest against a tier directory (or a --path override)."""
    target = args.path if args.path else tier_dir
    cmd = [str(VENV_PYTHON), "-m", "pytest", target]
    cmd.append("-v" if args.verbose else "-q")
    if args.fail_fast:
        cmd.append("-x")
    return _run(cmd, cwd=PROJECT_ROOT)


def step_unit(args) -> bool:
    _banner("pytest tests/unit")
    return step_pytest("tests/unit", args)


def step_integration(args) -> bool:
    _banner("pytest tests/integration")
    return step_pytest("tests/integration", args)


def step_e2e(args) -> bool:
    _banner("pytest tests/e2e")
    return step_pytest("tests/e2e", args)


def step_lint(args) -> bool:
    _banner("flake8")
    cmd = [str(VENV_FLAKE8), *FLAKE8_ARGS]
    if args.verbose:
        cmd.append("--verbose")
    cmd.append(".")
    return _run(cmd, cwd=PROJECT_ROOT)


def step_check(args) -> bool:
    _banner("manage.py check")
    cmd = [str(VENV_PYTHON), str(MANAGE_PY), "check"]
    return _run(cmd, env_overrides=DJANGO_ENV_OVERRIDES, cwd=PROJECT_ROOT)


def step_migrations(args) -> bool:
    _banner("manage.py makemigrations --dry-run --check")
    cmd = [
        str(VENV_PYTHON),
        str(MANAGE_PY),
        "makemigrations",
        "--dry-run",
        "--check",
    ]
    return _run(cmd, env_overrides=DJANGO_ENV_OVERRIDES, cwd=PROJECT_ROOT)


STEPS = {
    "unit": step_unit,
    "integration": step_integration,
    "e2e": step_e2e,
    "lint": step_lint,
    "check": step_check,
    "migrations": step_migrations,
}

PYTEST_STEPS = {"unit", "integration", "e2e"}

PRESETS = {
    "fast": ["unit", "lint"],
    "backend": ["unit", "integration", "lint", "check", "migrations"],
    "full": ["unit", "integration", "e2e", "lint", "check", "migrations"],
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="test.py",
        description="Sethlans Reborn test runner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "command",
        choices=list(STEPS.keys()) + list(PRESETS.keys()),
        help="Individual step or preset to run.",
    )
    p.add_argument(
        "--path", "-p",
        default=None,
        help="Pytest path filter (only valid with unit/integration/e2e).",
    )
    p.add_argument(
        "--fail-fast", "-x",
        action="store_true",
        help="Stop at the first failing step (and pass -x to pytest).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output (pytest -v instead of -q).",
    )
    p.add_argument(
        "--no-lint",
        action="store_true",
        help="Skip the lint step (presets only).",
    )
    p.add_argument(
        "--no-check",
        action="store_true",
        help="Skip the manage.py check step (presets only).",
    )
    p.add_argument(
        "--no-migrations",
        action="store_true",
        help="Skip the migrations check step (presets only).",
    )
    return p


def resolve_steps(args) -> list:
    """Turn the command + options into an ordered list of step names."""
    if args.command in PRESETS:
        steps = list(PRESETS[args.command])
        if args.no_lint:
            steps = [s for s in steps if s != "lint"]
        if args.no_check:
            steps = [s for s in steps if s != "check"]
        if args.no_migrations:
            steps = [s for s in steps if s != "migrations"]
        return steps
    return [args.command]


def validate_path_flag(args, steps: list) -> None:
    if args.path is None:
        return
    if len(steps) != 1 or steps[0] not in PYTEST_STEPS:
        sys.exit(
            "ERROR: --path is only valid with a single pytest tier "
            "(unit, integration, or e2e)."
        )


def print_summary(results: list) -> None:
    print("\n" + "=" * 70)
    print("== Summary")
    print("=" * 70)
    width = max(len(name) for name, _, _ in results)
    for name, ok, duration in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {name:<{width}}  {status}  ({duration:.1f}s)")
    total = sum(d for _, _, d in results)
    failed = [name for name, ok, _ in results if not ok]
    print("-" * 70)
    print(f"  total: {total:.1f}s, {len(results)} steps, {len(failed)} failed")
    if failed:
        print(f"  failed: {', '.join(failed)}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    steps = resolve_steps(args)
    validate_path_flag(args, steps)

    results = []
    for name in steps:
        start = time.monotonic()
        ok = STEPS[name](args)
        duration = time.monotonic() - start
        results.append((name, ok, duration))
        if not ok and args.fail_fast:
            break

    print_summary(results)
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
