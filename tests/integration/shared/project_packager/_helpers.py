# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the packager real-filesystem tests (Unreal foundation FR-T-6
"Packager"): a synthetic project shaped like the evidence directory and a
source snapshot.
"""
import hashlib
import os
import random
from pathlib import Path

NAME = 'ACOMDemo'

INCLUDED = {
    f'{NAME}.uproject': b'{"FileVersion": 3, "EngineAssociation": "5.8", "Plugins": [{"Name": "Fab"}]}',
    'Config/DefaultEngine.ini': b'[/Script/Engine.RendererSettings]\nr.X=1\n',
    'Config/DefaultGame.ini': b'[/Script/EngineSettings.GeneralProjectSettings]\n',
    'Content/Maps/Main.umap': random.Random(1).randbytes(300_000),
    'Content/Chars/Hero.uasset': random.Random(2).randbytes(70_000),
    'Content/Chars/Hero-Alt.uasset': random.Random(3).randbytes(1_000),
}
EMPTY_DIRS = ('Content/Empty',)

EXCLUDED = {
    'Content/.hidden': b'dotfile',
    'Content/.git/config': b'[core]\n',
    'Content/Thumbs.db': b'junk',
    'Content/Maps/desktop.ini': b'junk',
    'Content/.DS_Store': b'junk',
    'Saved/Logs/ACOMDemo.log': b'log\n',
    'Saved/MovieRenders/frame.png': b'png',
    'Intermediate/ProjectFiles/a.txt': b'a',
    'DerivedDataCache/x.udd': b'ddc',
    'Binaries/Win64/x.dll': b'dll',
    '.git/HEAD': b'ref: refs/heads/main\n',
    '.claude/settings.json': b'{}',
    '.mcp.json': b'{}',
    'bench.ps1': b'Write-Host hi\n',
}
EXCLUDED_TOP_LEVEL = ['.claude', '.git', '.mcp.json', 'Binaries', 'DerivedDataCache', 'Intermediate', 'Saved',
                      'bench.ps1']


def write_tree(root: Path, files: dict) -> None:
    for rel, data in files.items():
        path = root.joinpath(*rel.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def make_project(tmp_path: Path, *, extra: dict | None = None, name: str = NAME) -> Path:
    """The evidence-shaped project at ``tmp_path/src/<name>``."""
    root = Path(tmp_path) / 'src' / name
    files = dict(INCLUDED)
    if name != NAME:
        files[f'{name}.uproject'] = files.pop(f'{NAME}.uproject')
    files.update(EXCLUDED)
    files.update(extra or {})
    write_tree(root, files)
    for rel in EMPTY_DIRS:
        root.joinpath(*rel.split('/')).mkdir(parents=True, exist_ok=True)
    return root


def expected_names(name: str = NAME) -> list:
    """The archive members for ``make_project`` in archive order."""
    dirs = {'', 'Config', 'Content', 'Content/Chars', 'Content/Empty', 'Content/Maps'}
    names = [f'{name}/{d}/' if d else f'{name}/' for d in dirs]
    files = [rel.replace(NAME + '.uproject', name + '.uproject') for rel in INCLUDED]
    names += [f'{name}/{rel}' for rel in files]
    return sorted(names, key=lambda n: n.rstrip('/')) + ['sethlans-manifest.json']


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def snapshot(root: Path) -> list:
    """``(relpath, size, mtime_ns, sha256)`` of every file and each directory's mtime."""
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        st = os.stat(dirpath)
        rows.append((os.path.relpath(dirpath, root), 'dir', st.st_mtime_ns, ''))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            st = os.lstat(full)
            rows.append((os.path.relpath(full, root), st.st_size, st.st_mtime_ns, sha256_file(full)))
    return rows


def partials(directory: Path) -> list:
    return sorted(p.name for p in Path(directory).iterdir() if '.partial-' in p.name)
