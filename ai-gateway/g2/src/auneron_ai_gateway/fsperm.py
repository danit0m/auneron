"""Permissao da chave privada, falha fechada.

POSIX: dono = usuario do processo e modo sem bits de grupo/outros.
Windows: DACL lida por API (ctypes, sem depender do idioma): so podem ter ACE de "permitir"
o dono do arquivo, o usuario do processo, SYSTEM (S-1-5-18), Administradores (S-1-5-32-544)
e CREATOR OWNER (S-1-3-0). DACL nula, ACE de objeto/desconhecida ou qualquer outro SID: recusa.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class KeyPermissionError(Exception):
    """Permissao insatisfatoria ou nao verificavel."""


def posix_violation(mode: int, owner_uid: int, my_uid: int) -> str | None:
    if owner_uid != my_uid:
        return "dono da chave diferente do usuario do servico"
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return "chave acessivel a grupo/outros"
    if not stat.S_ISREG(mode):
        return "chave nao e arquivo regular"
    return None


def _check_posix(path: Path) -> None:
    st = os.stat(path)
    reason = posix_violation(st.st_mode, st.st_uid, os.getuid())
    if reason:
        raise KeyPermissionError(reason)


ALWAYS_OK_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-0"})
_ALLOW_SIMPLE = (0x00, 0x09)  # ACCESS_ALLOWED, ACCESS_ALLOWED_CALLBACK
_ALLOW_OBJECT = (0x05, 0x0B)
_INHERIT_ONLY = 0x08


def windows_violations(aces: list[tuple[int, int, str]], allowed_sids: set[str]) -> list[str]:
    """aces: (tipo, flags, sid). Funcao pura, testavel em qualquer SO."""
    bad = []
    for ace_type, flags, sid in aces:
        if flags & _INHERIT_ONLY:
            continue
        if ace_type in _ALLOW_SIMPLE:
            if sid not in allowed_sids and sid not in ALWAYS_OK_SIDS:
                bad.append(sid)
        elif ace_type in _ALLOW_OBJECT:
            bad.append("ace-de-objeto")
    return bad


def _read_windows_acl(path: Path) -> tuple[list[tuple[int, int, str]], set[str]]:
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    krn = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p

    adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.POINTER(vp),
    ]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.GetAce.argtypes = [vp, wintypes.DWORD, ctypes.POINTER(vp)]
    adv.GetAce.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = [vp, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, vp, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    adv.GetTokenInformation.restype = wintypes.BOOL
    krn.GetCurrentProcess.restype = wintypes.HANDLE
    krn.LocalFree.argtypes = [vp]
    krn.LocalFree.restype = vp
    krn.CloseHandle.argtypes = [wintypes.HANDLE]

    def sid_str(psid: int) -> str:
        out = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            raise KeyPermissionError("SID ilegivel")
        try:
            return str(out.value)
        finally:
            krn.LocalFree(ctypes.cast(out, vp))

    owner, dacl, sd = vp(), vp(), vp()
    rc = adv.GetNamedSecurityInfoW(
        str(path), 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd)
    )
    if rc != 0:
        raise KeyPermissionError("nao foi possivel ler a ACL da chave")
    try:
        if not dacl.value:
            raise KeyPermissionError("DACL nula (acesso total para todos)")
        allowed = {sid_str(owner.value)}
        # usuario do processo
        token = wintypes.HANDLE()
        if not adv.OpenProcessToken(krn.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise KeyPermissionError("token do processo ilegivel")
        try:
            need = wintypes.DWORD(0)
            adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))
            buf = ctypes.create_string_buffer(need.value)
            if not adv.GetTokenInformation(token, 1, buf, need, ctypes.byref(need)):
                raise KeyPermissionError("usuario do processo ilegivel")
            allowed.add(sid_str(ctypes.cast(buf, ctypes.POINTER(vp))[0]))
        finally:
            krn.CloseHandle(token)
        count = ctypes.c_ushort.from_address(dacl.value + 4).value  # ACL.AceCount
        aces: list[tuple[int, int, str]] = []
        for i in range(count):
            pace = vp()
            if not adv.GetAce(dacl, i, ctypes.byref(pace)):
                raise KeyPermissionError("ACE ilegivel")
            ace_type = ctypes.c_ubyte.from_address(pace.value).value
            flags = ctypes.c_ubyte.from_address(pace.value + 1).value
            if ace_type in _ALLOW_SIMPLE:
                aces.append((ace_type, flags, sid_str(pace.value + 8)))
            elif ace_type in _ALLOW_OBJECT:
                aces.append((ace_type, flags, "?"))
        return aces, allowed
    finally:
        krn.LocalFree(sd)


def _check_windows(path: Path) -> None:
    aces, allowed = _read_windows_acl(path)
    bad = windows_violations(aces, allowed)
    if bad:
        raise KeyPermissionError("ACL da chave concede acesso a outros: " + ", ".join(sorted(set(bad))))


def check_private_key_permissions(path: Path) -> None:
    try:
        if os.name == "nt":
            _check_windows(Path(path))
        else:
            _check_posix(Path(path))
    except KeyPermissionError:
        raise
    except Exception:  # qualquer falha de leitura: nao verificavel => recusa
        raise KeyPermissionError("permissao da chave nao verificavel") from None
