# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager's cache of Blender builds for its workers (roadmap theme F, D7).

The manager keeps one official archive of each supported Blender version for
each platform its workers run on, and serves it to enrolled workers
(``views.blender_builds``):

- ``wanted``: the wanted set and every row write outside a fetch;
- ``fetcher``: one claimed fetch (release list, download, placement);
- ``sweep``: removal of the files no row names;
- ``runner``: the one thread that fetches and sweeps, and its start steps;
- ``paths``: the cache root and read-only file checks.

Only the runner thread touches files under the cache root.
"""
