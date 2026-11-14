# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Known-folder probe, run as a short child process in the FR-UE-27
isolation environment (Unreal foundation FR-UE-27, research §2, §4).

It resolves ``FOLDERID_LocalAppData``, ``FOLDERID_RoamingAppData`` and
``FOLDERID_Documents`` with ``SHGetKnownFolderPath`` and the temp path
with ``GetTempPathW``, and prints **one** JSON line::

    {"schema": 1, "folders": {"local_appdata": "...", "roaming_appdata": "...",
     "documents": "...", "temp": "..."}, "errors": {"documents": "0x80070003"}}

A folder whose call fails is ``null`` in ``folders`` with its HRESULT in
``errors``. The worker (``user_state.check_known_folders``) compares the
paths with the farm profile.

**Self-contained on purpose:** it imports only the standard library, so it
runs as ``python -I <this file>`` from source (isolated mode does not put
the worker package on ``sys.path``) and through the reserved first
argument ``--sethlans-known-folder-probe`` of a frozen worker, which
``run_worker.py`` handles before any other import or startup work.
"""
import json
import sys

SCHEMA = 1
# GUIDs from KnownFolders.h.
FOLDER_IDS = (
    ('local_appdata', '{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}'),
    ('roaming_appdata', '{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}'),
    ('documents', '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}'),
)
_KF_FLAG_DEFAULT = 0


def _known_folder(ctypes, shell32, ole32, guid_text):
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_ubyte * 8)]

    guid = GUID()
    hr = ole32.CLSIDFromString(guid_text, ctypes.byref(guid))
    if hr != 0:
        return None, f"0x{hr & 0xFFFFFFFF:08X}"
    out = ctypes.c_void_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), _KF_FLAG_DEFAULT, None, ctypes.byref(out))
    try:
        if hr != 0 or not out.value:
            return None, f"0x{hr & 0xFFFFFFFF:08X}"
        return ctypes.wstring_at(out.value), ''
    finally:
        if out.value:
            ole32.CoTaskMemFree(out)


def _temp_path(ctypes, kernel32):
    size = kernel32.GetTempPathW(0, None)
    if not size:
        return None, f"0x{ctypes.get_last_error():08X}"
    buf = ctypes.create_unicode_buffer(size + 1)
    written = kernel32.GetTempPathW(size + 1, buf)
    if not written or written > size:
        return None, f"0x{ctypes.get_last_error():08X}"
    return buf.value, ''


def probe() -> dict:
    """Resolve the four folders (Windows only)."""
    folders, errors = {}, {}
    if sys.platform != 'win32':
        return {'schema': SCHEMA, 'folders': {}, 'errors': {'platform': sys.platform}}
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    kernel32.GetTempPathW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel32.GetTempPathW.restype = wintypes.DWORD
    for name, guid_text in FOLDER_IDS:
        folders[name], error = _known_folder(ctypes, shell32, ole32, guid_text)
        if error:
            errors[name] = error
    folders['temp'], error = _temp_path(ctypes, kernel32)
    if error:
        errors['temp'] = error
    return {'schema': SCHEMA, 'folders': folders, 'errors': errors}


def main() -> int:
    try:
        result = probe()
    except Exception as exc:  # noqa: BLE001 - report, never a traceback on stdout
        result = {'schema': SCHEMA, 'folders': {}, 'errors': {'probe': type(exc).__name__}}
    sys.stdout.write(json.dumps(result) + '\n')
    sys.stdout.flush()
    return 0 if not result.get('errors') else 1


if __name__ == '__main__':
    sys.exit(main())
