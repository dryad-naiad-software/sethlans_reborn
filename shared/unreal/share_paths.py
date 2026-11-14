# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Paths relative to the Sethlans share (Unreal foundation FR-MU-5, FR-MU-15).

Assets store ``archive_path`` and ``overlay_path`` **relative to the share
root**, never as a full UNC path, so an asset cannot name a host.

* :func:`validate_share_relpath` applies the FR-MU-15 rules to one
  relative path and returns its normalized ``/``-separated form.
* :func:`resolve_in_share` joins a validated relative path to a share root
  and re-validates the result as a UNC path (FR-MU-6). Callers compare
  paths only through this join of a root and a validated relative path,
  never by testing an independently supplied full path for containment.

A violation raises ``ValueError`` whose message names the rule. Nothing
here touches the filesystem or the network.
"""
import re

from .unc import UncPath, _check_component, validate_unc_dir, validate_unc_path

MAX_LENGTH = 512
MAX_COMPONENTS = 20
MAX_COMPONENT_LENGTH = 255

_DRIVE = re.compile(r'^[A-Za-z]:', re.ASCII)
# Windows reserved device names (FR-PA-6 rule 2), with or without an
# extension: CON, PRN, AUX, NUL, CONIN$, CONOUT$, COM1-9, LPT1-9 and
# COM/LPT followed by a superscript 1, 2 or 3.
_DEVICE_NAME = re.compile(
    r'^(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[1-9¹²³])\Z',
    re.IGNORECASE,
)


def _is_device_name(component: str) -> bool:
    stem = component.split('.', 1)[0].rstrip(' ')
    return bool(_DEVICE_NAME.match(stem))


def _check_text(value) -> None:
    if not isinstance(value, str):
        raise ValueError("share-relative path must be a string (rule 1)")
    if not 1 <= len(value) <= MAX_LENGTH:
        raise ValueError(f"share-relative path length must be 1-{MAX_LENGTH} characters (rule 1)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("share-relative path contains a control character (rule 1)")
    for prefix in ('\\\\', '//'):
        if value.startswith(prefix) or value.replace('\\', '/').startswith(prefix):
            raise ValueError("share-relative path must not be a UNC or device path (rule 3)")
    if _DRIVE.match(value):
        raise ValueError("share-relative path must not start with a drive letter (rule 3)")


def _check_rel_component(part: str) -> None:
    if part == '':
        raise ValueError("share-relative path has an empty component (rule 3)")
    if part in ('.', '..'):
        raise ValueError(f"component {part!r} is not allowed (rule 3)")
    if len(part) > MAX_COMPONENT_LENGTH:
        raise ValueError(f"a component is longer than {MAX_COMPONENT_LENGTH} characters (rule 4)")
    _check_component(part)
    if _is_device_name(part):
        raise ValueError(f"component {part!r} is a reserved Windows device name (rule 4)")


def validate_share_relpath(value, *, kind: str, suffix: str | None = None) -> str:
    """Validate a path relative to the Sethlans share root (FR-MU-15).

    Args:
        value: The path as entered; ``\\`` is accepted and normalized to ``/``.
        kind: ``'file'`` or ``'dir'``.
        suffix: For ``kind='file'``, the required suffix of the last
            component (case-insensitive), for example ``'.zip'``.

    Returns:
        The normalized ``/``-separated form.

    Raises:
        ValueError: a rule is violated; the message names it.
    """
    if kind not in ('file', 'dir'):
        raise ValueError(f"kind must be 'file' or 'dir', not {kind!r}")
    _check_text(value)
    normalized = value.replace('\\', '/')
    if normalized.startswith('/'):
        raise ValueError("share-relative path must not be absolute (rule 3)")
    parts = normalized.split('/')
    if not 1 <= len(parts) <= MAX_COMPONENTS:
        raise ValueError(f"share-relative path must have 1-{MAX_COMPONENTS} components (rule 4)")
    for part in parts:
        _check_rel_component(part)
    if kind == 'file' and suffix:
        last = parts[-1]
        if not last.lower().endswith(suffix.lower()) or len(last) <= len(suffix):
            raise ValueError(f"file name must end with {suffix!r} (rule 5)")
    return normalized


def resolve_in_share(root, relpath: str) -> UncPath:
    """Join ``relpath`` to the share ``root`` and validate the result.

    ``relpath`` is re-validated (FR-MU-15), joined to the canonical root
    with ``\\`` and the result validated as a resolved UNC path (FR-MU-6,
    which needs at least one component after the share and caps the
    length at 1024). The containment check is defense in depth: the
    relative rules already make a lexical escape impossible.

    Args:
        root: The share root, an ``UncPath`` or a string that passes
            ``validate_unc_dir``.
        relpath: A share-relative path.

    Raises:
        ValueError: ``relpath`` or the joined path breaks a rule, or the
            result is outside ``root``.
    """
    if not isinstance(root, UncPath):
        root = validate_unc_dir(root)
    normalized = validate_share_relpath(relpath, kind='dir')
    joined = root.canonical + '\\' + normalized.replace('/', '\\')
    result = validate_unc_path(joined)
    if not result.is_within(root):
        raise ValueError("path resolves outside the Sethlans share")
    return result
