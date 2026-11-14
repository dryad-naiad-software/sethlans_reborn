# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows security-descriptor calls for ``shared.owner_only_acl`` (ctypes only).

``ctypes.wintypes`` and the DLLs are loaded on first use, inside ``_api``,
so importing this module is safe on every platform. Callers go through
``shared.owner_only_acl``; nothing else should import this module.
"""
import threading
from typing import List, Optional, Tuple

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_ACCESS_ALLOWED_ACE_TYPE = 0
_OBJECT_INHERIT_ACE = 0x1
_CONTAINER_INHERIT_ACE = 0x2
_REQUIRED_ACE_FLAGS = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_TOKEN_OWNER_CLASS = 4
_SDDL_REVISION_1 = 1
_SID_OFFSET_IN_ACE = 8  # ACE_HEADER (4 bytes) + ACCESS_MASK (4 bytes)
_ERROR_ALREADY_EXISTS = 183

# One ACE: (ace_type, ace_flags, sid string or '' for a non-allowed ACE).
Ace = Tuple[int, int, str]

_api_cache = {}
_api_lock = threading.Lock()  # leaf lock


def _api():
    """Load advapi32/kernel32 with argument types (Windows only, cached)."""
    with _api_lock:
        if _api_cache:
            return _api_cache['ns']
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        vp, dword, boolean = ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL
        pvp, pdword, pbool = ctypes.POINTER(vp), ctypes.POINTER(dword), ctypes.POINTER(boolean)
        sigs = (
            (kernel32.GetCurrentProcess, [], wintypes.HANDLE),
            (kernel32.CloseHandle, [wintypes.HANDLE], boolean),
            (kernel32.LocalFree, [vp], vp),
            (advapi32.OpenProcessToken, [wintypes.HANDLE, dword, ctypes.POINTER(wintypes.HANDLE)], boolean),
            (advapi32.GetTokenInformation, [wintypes.HANDLE, ctypes.c_int, vp, dword, pdword], boolean),
            (advapi32.ConvertSidToStringSidW, [vp, pvp], boolean),
            (advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW,
             [wintypes.LPCWSTR, dword, pvp, pdword], boolean),
            (advapi32.GetSecurityDescriptorDacl, [vp, pbool, pvp, pbool], boolean),
            (advapi32.GetSecurityDescriptorOwner, [vp, pvp, pbool], boolean),
            (advapi32.GetSecurityDescriptorControl, [vp, ctypes.POINTER(wintypes.WORD), pdword], boolean),
            (advapi32.SetNamedSecurityInfoW, [wintypes.LPWSTR, ctypes.c_int, dword, vp, vp, vp, vp], dword),
            (advapi32.GetNamedSecurityInfoW,
             [wintypes.LPCWSTR, ctypes.c_int, dword, pvp, pvp, pvp, pvp, pvp], dword),
            (advapi32.GetAce, [vp, dword, pvp], boolean),
            (kernel32.CreateDirectoryW, [wintypes.LPCWSTR, vp], boolean),
        )
        for func, argtypes, restype in sigs:
            func.argtypes, func.restype = argtypes, restype
        ns = {'ctypes': ctypes, 'wintypes': wintypes, 'advapi32': advapi32, 'kernel32': kernel32}
        _api_cache['ns'] = ns
        return ns


def _last_error(api, what: str) -> OSError:
    code = api['ctypes'].get_last_error()
    return OSError(code, f"{what} failed (Windows error {code})")


def _sid_to_string(api, psid) -> str:
    ctypes = api['ctypes']
    out = ctypes.c_void_p()
    if not api['advapi32'].ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise _last_error(api, 'ConvertSidToStringSidW')
    try:
        return ctypes.wstring_at(out.value)
    finally:
        api['kernel32'].LocalFree(out)


def _token_sid(info_class: int) -> str:
    """The SID in ``TOKEN_USER`` or ``TOKEN_OWNER``: both start with a ``PSID``."""
    api = _api()
    ctypes, wintypes = api['ctypes'], api['wintypes']
    advapi32, kernel32 = api['advapi32'], api['kernel32']
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _last_error(api, 'OpenProcessToken')
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(max(size.value, 1))
        if not advapi32.GetTokenInformation(token, info_class, buf, size, ctypes.byref(size)):
            raise _last_error(api, 'GetTokenInformation')
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid / TOKEN_OWNER.Owner
        return _sid_to_string(api, psid)
    finally:
        kernel32.CloseHandle(token)


def token_user_sid() -> str:
    """SID of the process token's user."""
    return _token_sid(_TOKEN_USER_CLASS)


def token_owner_sid() -> str:
    """The token's default owner for new objects (Administrators, S-1-5-32-544, when elevated)."""
    return _token_sid(_TOKEN_OWNER_CLASS)


def create_dir(path: str, sddl: str) -> bool:
    """``CreateDirectoryW`` with ``sddl`` as its security descriptor from birth.

    True when created, False when it already exists (``ERROR_ALREADY_EXISTS``).
    """
    api = _api()
    ctypes, wintypes = api['ctypes'], api['wintypes']

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p),
                    ('bInheritHandle', wintypes.BOOL)]

    psd = ctypes.c_void_p()
    if not api['advapi32'].ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(psd), None):
        raise _last_error(api, 'ConvertStringSecurityDescriptorToSecurityDescriptorW')
    try:
        attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), psd, False)
        if api['kernel32'].CreateDirectoryW(path, ctypes.byref(attrs)):
            return True
        code = ctypes.get_last_error()
        if code == _ERROR_ALREADY_EXISTS:
            return False
        raise OSError(code, f"CreateDirectoryW failed (Windows error {code})")
    finally:
        api['kernel32'].LocalFree(psd)


def set_security(path: str, sddl: str) -> None:
    """Apply the owner and protected DACL described by ``sddl`` to ``path``."""
    api = _api()
    ctypes, wintypes, advapi32 = api['ctypes'], api['wintypes'], api['advapi32']
    psd = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(psd), None):
        raise _last_error(api, 'ConvertStringSecurityDescriptorToSecurityDescriptorW')
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        pdacl, powner = ctypes.c_void_p(), ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(psd, ctypes.byref(present), ctypes.byref(pdacl),
                                                  ctypes.byref(defaulted)):
            raise _last_error(api, 'GetSecurityDescriptorDacl')
        if not advapi32.GetSecurityDescriptorOwner(psd, ctypes.byref(powner), ctypes.byref(defaulted)):
            raise _last_error(api, 'GetSecurityDescriptorOwner')
        if not present.value or not pdacl.value or not powner.value:
            raise OSError("security descriptor lacks an owner or a DACL")
        # DACL first: an owner holds WRITE_DAC implicitly but not WRITE_OWNER,
        # which the new DACL's FA then grants for the owner step.
        steps = ((_DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION, None, pdacl),
                 (_OWNER_SECURITY_INFORMATION, powner, None))
        for info, owner_arg, dacl_arg in steps:
            code = advapi32.SetNamedSecurityInfoW(path, _SE_FILE_OBJECT, info, owner_arg, None, dacl_arg, None)
            if code != 0:
                raise OSError(code, f"SetNamedSecurityInfoW failed (Windows error {code})")
    finally:
        api['kernel32'].LocalFree(psd)


def _read_aces(api, pdacl) -> List[Ace]:
    ctypes = api['ctypes']
    ace_count = ctypes.cast(pdacl, ctypes.POINTER(ctypes.c_uint16))[2]  # ACL.AceCount
    aces = []
    for index in range(ace_count):
        pace = ctypes.c_void_p()
        if not api['advapi32'].GetAce(pdacl, index, ctypes.byref(pace)):
            raise _last_error(api, 'GetAce')
        header = ctypes.cast(pace, ctypes.POINTER(ctypes.c_uint8))
        ace_type, ace_flags = header[0], header[1]
        sid = ''
        if ace_type == _ACCESS_ALLOWED_ACE_TYPE:
            sid = _sid_to_string(api, ctypes.c_void_p(pace.value + _SID_OFFSET_IN_ACE))
        aces.append((ace_type, ace_flags, sid))
    return aces


def read_security(path: str) -> Tuple[bool, str, Optional[List[Ace]]]:
    """Return ``(dacl_protected, owner_sid, aces)``; ``aces`` is None for a NULL DACL."""
    api = _api()
    ctypes, wintypes, advapi32 = api['ctypes'], api['wintypes'], api['advapi32']
    powner, pdacl, psd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = advapi32.GetNamedSecurityInfoW(
        path, _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(powner), None, ctypes.byref(pdacl), None, ctypes.byref(psd))
    if code != 0:
        raise OSError(code, f"GetNamedSecurityInfoW failed (Windows error {code})")
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(psd, ctypes.byref(control), ctypes.byref(revision)):
            raise _last_error(api, 'GetSecurityDescriptorControl')
        owner = _sid_to_string(api, powner) if powner.value else ''
        aces = _read_aces(api, pdacl) if pdacl.value else None
        return bool(control.value & _SE_DACL_PROTECTED), owner, aces
    finally:
        api['kernel32'].LocalFree(psd)
