"""Conferencia de que o Ollama NAO escuta fora de loopback (IPv4 e IPv6).

1) Tabela de escuta do sistema (Windows: GetExtendedTcpTable; Linux: /proc/net/tcp*) - autoritativa.
2) Se a tabela nao estiver disponivel: sondagem por conexao em todos os enderecos locais nao
   loopback (IPv4 e IPv6, incluindo link-local com escopo de cada interface).
3) Se nada for verificavel: recusa por padrao (politica "refuse"); "allow" so por decisao no TOML.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from collections.abc import Callable

Listener = tuple[str, int]  # (endereco, porta)


class ExposureError(Exception):
    pass


def _port_from_net(p: int) -> int:
    return ((p & 0xFF) << 8) | ((p >> 8) & 0xFF)


def listeners_windows() -> list[Listener] | None:
    import ctypes
    from ctypes import wintypes

    try:
        iph = ctypes.WinDLL("iphlpapi")
    except OSError:
        return None
    iph.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.ULONG, ctypes.c_int, wintypes.ULONG,
    ]
    iph.GetExtendedTcpTable.restype = wintypes.DWORD
    out: list[Listener] = []
    for af, row_fmt in ((2, "<IIIIII"), (23, "<16sII16sIIII")):
        size = wintypes.DWORD(0)
        iph.GetExtendedTcpTable(None, ctypes.byref(size), False, af, 3, 0)
        ok = False
        for _ in range(5):
            buf = ctypes.create_string_buffer(size.value + 4096)
            size = wintypes.DWORD(len(buf))
            rc = iph.GetExtendedTcpTable(buf, ctypes.byref(size), False, af, 3, 0)
            if rc == 0:
                ok = True
                break
            if rc != 122:  # ERROR_INSUFFICIENT_BUFFER
                return None
        if not ok:
            return None
        n = struct.unpack_from("<I", buf, 0)[0]
        rsize = struct.calcsize(row_fmt)
        for i in range(n):
            row = struct.unpack_from(row_fmt, buf, 4 + i * rsize)
            if af == 2:
                _state, laddr, lport = row[0], row[1], row[2]
                out.append((socket.inet_ntoa(struct.pack("<I", laddr)), _port_from_net(lport & 0xFFFF)))
            else:
                laddr, scope, lport = row[0], row[1], row[2]
                addr = str(ipaddress.IPv6Address(laddr))
                out.append((addr, _port_from_net(lport & 0xFFFF)))
    return out


def parse_proc_net(text: str, v6: bool) -> list[Listener]:
    out: list[Listener] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":  # LISTEN
            continue
        host_hex, port_hex = parts[1].split(":")
        raw = bytes.fromhex(host_hex)
        if v6:
            raw = b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4))
            addr = str(ipaddress.IPv6Address(raw))
        else:
            addr = socket.inet_ntoa(raw[::-1])
        out.append((addr, int(port_hex, 16)))
    return out


def listeners_linux() -> list[Listener] | None:
    out: list[Listener] = []
    try:
        for name, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
            with open(name, encoding="ascii") as fh:
                out += parse_proc_net(fh.read(), v6)
    except OSError:
        return None
    return out


def system_listeners() -> list[Listener] | None:
    return listeners_windows() if os.name == "nt" else listeners_linux()


def classify(addr: str) -> str:
    """"loopback", "wildcard" ou "other"."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return "other"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return "wildcard"
    if ip.is_loopback:
        return "loopback"
    return "other"


def local_candidate_addresses() -> list[str]:
    """Enderecos locais nao loopback (IPv4 e IPv6; link-local com escopo de cada interface)."""
    found: set[str] = set()
    name = socket.gethostname()
    try:
        for info in socket.getaddrinfo(name, None, socket.AF_INET):
            found.add(info[4][0])
        found.update(socket.gethostbyname_ex(name)[2])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(name, None, socket.AF_INET6):
            found.add(info[4][0].split("%")[0])
    except OSError:
        pass
    try:
        indexes = [i for i, _ in socket.if_nameindex()]
    except (OSError, AttributeError):
        indexes = []
    out: list[str] = []
    for a in sorted(found):
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_unspecified:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.is_link_local:
            out += [f"{a}%{i}" for i in indexes] or [a]
        else:
            out.append(a)
    return out


def _probe(addr: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ollama_not_exposed(
    port: int,
    *,
    listeners: Callable[[], list[Listener] | None] = system_listeners,
    addresses: Callable[[], list[str]] = local_candidate_addresses,
    probe: Callable[[str, int, float], bool] = _probe,
    on_unverifiable: str = "refuse",
    timeout: float = 1.0,
) -> str:
    """Retorna "ok_table", "ok_not_listening", "ok_probe" ou "unverifiable_allowed".

    Levanta ExposureError se exposto ou (politica refuse) nao verificavel.
    """
    table = listeners()
    if table is not None:
        mine = [a for a, p in table if p == port]
        for a in mine:
            if classify(a) != "loopback":
                raise ExposureError("ollama escuta fora de loopback")
        return "ok_table" if mine else "ok_not_listening"
    addrs = addresses()
    if addrs:
        for a in addrs:
            if probe(a, port, timeout):
                raise ExposureError("ollama escuta fora de loopback")
        return "ok_probe"
    if on_unverifiable == "allow":
        return "unverifiable_allowed"
    raise ExposureError("exposicao do ollama nao verificavel (politica: recusar)")
