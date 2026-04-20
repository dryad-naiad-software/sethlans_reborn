# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the Linux installer (makeself .run + .desktop).

Covers GitHub issue #92: after ``sudo ./sethlans-<v>-linux-x64.run`` on
Ubuntu 24.04 + GNOME 46 (Wayland), the app failed to appear in the GNOME
app grid. Root cause: ``packaging/linux/install.sh`` ran ``mkdir -p
"${PREFIX}"`` under makeself's root umask, leaving ``/opt/sethlans/`` at
mode 0700. Non-root processes (the GNOME user's Shell, gtk-launch) could
not traverse the directory to validate the ``Exec=`` path, so
``Gio.DesktopAppInfo.new_from_filename`` silently rejected the .desktop
file.

Two complementary static tests:

1. The source ``sethlans.desktop`` is well-formed for GAppInfo loaders
   (parses cleanly, declares the keys GNOME requires, and sits in the
   ``Graphics`` category).
2. ``install.sh`` explicitly forces world-traversable perms on
   ``${PREFIX}`` immediately after the ``mkdir -p``, locking in the fix.

Both tests are static — they read the repo source and never touch
``/opt/sethlans/`` (which would require sudo + an extracted .run).

Fixes #92.
"""

import configparser
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LINUX_DIR = REPO_ROOT / "packaging" / "linux"
DESKTOP_FILE = LINUX_DIR / "sethlans.desktop"
INSTALL_SCRIPT = LINUX_DIR / "install.sh"


@pytest.fixture(scope="module")
def desktop_text() -> str:
    assert DESKTOP_FILE.is_file(), f"Expected .desktop file at {DESKTOP_FILE}"
    return DESKTOP_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def desktop_parsed(desktop_text: str) -> configparser.ConfigParser:
    # GAppInfo's loader is INI-shaped: a single [Desktop Entry] group of
    # Key=Value lines with `#` comments. configparser models that closely
    # enough to catch malformed files. SPDX header lines (`# SPDX-...`) are
    # legal `#` comments and must not break parsing.
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(desktop_text)
    return parser


@pytest.fixture(scope="module")
def install_text() -> str:
    assert INSTALL_SCRIPT.is_file(), f"Expected install script at {INSTALL_SCRIPT}"
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


class TestDesktopFileWellFormed:
    """Source .desktop file must remain loadable by GAppInfo / gtk-launch."""

    def test_desktop_file_parses_cleanly(
        self, desktop_parsed: configparser.ConfigParser
    ) -> None:
        # If parsing throws, the GNOME loader will reject the file with no
        # user-visible error other than "app missing from grid" — exactly
        # the failure mode of #92.
        assert desktop_parsed.sections(), (
            "Expected at least one section in sethlans.desktop; the file "
            "must parse as a valid Desktop Entry (issue #92)."
        )

    def test_desktop_entry_group_present(
        self, desktop_parsed: configparser.ConfigParser
    ) -> None:
        # The freedesktop.org Desktop Entry Spec requires the [Desktop Entry]
        # group as the very first group. GNOME's loader will reject any file
        # missing it.
        assert desktop_parsed.has_section("Desktop Entry"), (
            "Expected [Desktop Entry] group in sethlans.desktop "
            "(freedesktop.org Desktop Entry Specification)."
        )

    def test_type_is_application(
        self, desktop_parsed: configparser.ConfigParser
    ) -> None:
        # Type=Application is what makes the entry show up in the app grid.
        # Type=Link/Directory would silently exclude it.
        assert desktop_parsed.get("Desktop Entry", "Type") == "Application", (
            "Expected Type=Application so GNOME indexes Sethlans into the "
            "app grid."
        )

    def test_required_keys_present_and_non_empty(
        self, desktop_parsed: configparser.ConfigParser
    ) -> None:
        # Name, Exec, Icon, Categories are the keys the GNOME app grid
        # consults to render an entry. A missing or empty value here is
        # the same class of bug as #92 from the user's perspective: the
        # icon is gone.
        for key in ("Name", "Exec", "Icon", "Categories"):
            value = desktop_parsed.get("Desktop Entry", key, fallback="")
            assert value.strip(), (
                f"Expected non-empty {key}= in sethlans.desktop; GNOME "
                "requires this for app grid entries."
            )

    def test_categories_includes_graphics(
        self, desktop_parsed: configparser.ConfigParser
    ) -> None:
        # Categories is a `;`-separated list per the spec. Sethlans is a
        # rendering tool — Graphics is the category Software / app stores
        # filter on. Drift here would silently miscategorize the app.
        raw = desktop_parsed.get("Desktop Entry", "Categories", fallback="")
        categories = [c for c in raw.split(";") if c]
        assert "Graphics" in categories, (
            "Expected Categories= to include 'Graphics'; current value: "
            f"{raw!r}."
        )


class TestInstallScriptForcesPrefixPermissions:
    """install.sh must lock /opt/sethlans/ to 0755 right after mkdir -p.

    Regression guard for #92: makeself runs install.sh under root with a
    restrictive umask, so ``mkdir -p "${PREFIX}"`` produces a 0700 dir.
    The fix is an explicit ``chmod 0755 "${PREFIX}"`` immediately after.
    Without it, non-root processes can't traverse /opt/sethlans/ and the
    .desktop entry silently disappears from the GNOME app grid.
    """

    # Match `mkdir -p "${PREFIX}"`, tolerating tab/space and either
    # ${PREFIX} or "${PREFIX}".
    _MKDIR_RE = re.compile(
        r'^\s*mkdir\s+-p\s+"?\$\{PREFIX\}"?\s*$',
        re.MULTILINE,
    )
    # Match `chmod 0755 "${PREFIX}"` or `chmod 755 "${PREFIX}"`.
    _CHMOD_RE = re.compile(
        r'^\s*chmod\s+0?755\s+"?\$\{PREFIX\}"?\s*$',
        re.MULTILINE,
    )

    def test_mkdir_prefix_line_present(self, install_text: str) -> None:
        # Sanity check: the script must actually create ${PREFIX}. If this
        # ever moves or is renamed, the regression guard below is no
        # longer meaningful and the test should be updated, not deleted.
        assert self._MKDIR_RE.search(install_text), (
            "Expected `mkdir -p \"${PREFIX}\"` in install.sh as the "
            "anchor for the chmod 0755 regression guard (issue #92)."
        )

    def test_chmod_0755_follows_mkdir_within_5_lines(
        self, install_text: str
    ) -> None:
        lines = install_text.splitlines()
        mkdir_line_indices = [
            i for i, line in enumerate(lines)
            if self._MKDIR_RE.match(line)
        ]
        assert mkdir_line_indices, (
            "Expected at least one `mkdir -p \"${PREFIX}\"` line in "
            "install.sh — the fix for #92 anchors on it."
        )

        for mkdir_idx in mkdir_line_indices:
            # Inspect the ~5 lines immediately following the mkdir for
            # the explicit chmod. Co-locating them is what protects the
            # fix from being undone by a future cleanup that "tidies up"
            # the chmod into a separate block.
            window = lines[mkdir_idx + 1: mkdir_idx + 6]
            window_text = "\n".join(window)
            assert self._CHMOD_RE.search(window_text), (
                "Regression: install.sh has `mkdir -p \"${PREFIX}\"` at "
                f"line {mkdir_idx + 1} but no `chmod 0755 \"${{PREFIX}}\"` "
                "(or `chmod 755 ...`) within the next 5 lines. Without "
                "it, makeself's root umask leaves /opt/sethlans/ at 0700 "
                "and the GNOME app grid drops the entry (issue #92)."
            )


