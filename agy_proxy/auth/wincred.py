"""
Dependency-free Windows Credential Manager backend.

Mirrors the two keyring calls the proxy makes (service, username) without
needing the `keyring` package at runtime, so frozen/PyInstaller builds can
still read pool credentials. Target name matches the keyring win32 backend
convention: "<username>@<service>", blob is UTF-16LE.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIAL_ATTRIBUTE(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_byte)),
    ]


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTE)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True) if sys.platform == "win32" else None

if _advapi32 is not None:
    _advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL)),
    ]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def is_supported() -> bool:
    return _advapi32 is not None


def _target(service: str, username: str) -> str:
    return f"{username}@{service}"


def get_password(service: str, username: str) -> str | None:
    """Return the stored blob for (service, username), or None."""
    if _advapi32 is None:
        return None
    ptr = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi32.CredReadW(_target(service, username), CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        cred = ptr.contents
        size = cred.CredentialBlobSize
        if not size:
            return ""
        raw = ctypes.string_at(cred.CredentialBlob, size)
    finally:
        _advapi32.CredFree(ptr)
    # keyring win32 backend writes UTF-16LE; tolerate plain UTF-8 too.
    if size % 2 == 0:
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def set_password(service: str, username: str, value: str) -> bool:
    """Store value for (service, username). Returns True on success."""
    if _advapi32 is None:
        return False
    blob = value.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = _CREDENTIAL()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = _target(service, username)
    cred.UserName = username
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    return bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))


if __name__ == "__main__":
    # ponytail: runnable self-check against a throwaway credential
    assert is_supported(), "not on Windows"
    assert set_password("agy_proxy.selftest", "probe", '{"a":1}'), "write failed"
    assert get_password("agy_proxy.selftest", "probe") == '{"a":1}', "read mismatch"
    _advapi32.CredDeleteW(_target("agy_proxy.selftest", "probe"), CRED_TYPE_GENERIC, 0)
    assert get_password("agy_proxy.selftest", "probe") is None, "delete failed"
    print("ok")
