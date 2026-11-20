# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``request_body`` lines of the manager Caddyfile (issue #260).

Generated from :data:`sethlans_manager.body_limits.BODY_CLASSES`: one
named matcher and one ``request_body`` per class, then a small matcher
that excludes every class's paths. The static Docker Caddyfile carries
the same text (a unit test keeps them equal).
"""

from __future__ import annotations

from sethlans_manager import body_limits


def _block(head: str, line: str) -> list:
    return [f"    {head} {{", f"        {line}", "    }"]


def body_limit_directives(*, allow_uploads: bool) -> str:
    """Return the ``request_body`` lines for one site block.

    Caddy stops reading a body once it passes the limit and answers
    413, for ``Content-Length`` and chunked bodies alike. With
    ``allow_uploads`` each class of :mod:`body_limits` keeps its own
    limit and every other path gets the small one; without it every
    path gets the small one.
    """
    small = body_limits.SMALL_BODY_LIMIT
    if not allow_uploads:
        return '\n'.join(_block('request_body', f'max_size {small}')) + '\n'
    classes = body_limits.BODY_CLASSES
    every_path = ' '.join(path for body_class in classes for path in body_class.paths)
    lines = [
        "    # Request body limits (issue #260): each path class has its",
        "    # own limit, every other path gets the small one.",
    ]
    for body_class in classes:
        lines += _block(f'@{body_class.name}_body', 'path ' + ' '.join(body_class.paths))
    lines += _block('@small_body', f'not path {every_path}')
    for body_class in classes:
        lines += _block(f'request_body @{body_class.name}_body', f'max_size {body_class.limit}')
    lines += _block('request_body @small_body', f'max_size {small}')
    return '\n'.join(lines) + '\n'
