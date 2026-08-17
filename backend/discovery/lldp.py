"""Passive LLDP neighbor discovery on a given interface, via tcpdump.

A background thread runs `tcpdump -w -` continuously and parses LLDP
frames from its pcap-format stdout, caching the most recent neighbor
per interface. API handlers read the cache directly so requests never
block waiting on the network.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time

_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_LLDP_ETHERTYPE = 0x88CC
_PCAP_GLOBAL_HEADER_LEN = 24
_PCAP_RECORD_HEADER_LEN = 16
_RESTART_DELAY_SECONDS = 5

_lock = threading.Lock()
_neighbors: dict[str, dict] = {}
_started_interfaces: set[str] = set()


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _parse_chassis_id(subtype: int, value: bytes) -> str:
    if subtype == 4 and len(value) == 6:  # MAC address
        return ":".join(f"{b:02x}" for b in value)
    return value.decode("utf-8", errors="replace")


def _parse_port_id(subtype: int, value: bytes) -> str:
    if subtype == 3 and len(value) == 6:  # MAC address
        return ":".join(f"{b:02x}" for b in value)
    return value.decode("utf-8", errors="replace")


def _parse_management_address(value: bytes) -> str | None:
    if len(value) < 2:
        return None
    addr_len = value[0]
    addr_subtype = value[1]
    addr = value[2:1 + addr_len]
    if addr_subtype == 1 and len(addr) == 4:  # IPv4
        return ".".join(str(b) for b in addr)
    if addr_subtype == 2 and len(addr) == 16:  # IPv6
        return ":".join(addr[i:i + 2].hex() for i in range(0, 16, 2))
    return None


def _parse_lldpdu(payload: bytes) -> dict:
    neighbor = {
        "chassis_id": None,
        "port_id": None,
        "port_description": None,
        "system_name": None,
        "system_description": None,
        "management_ip": None,
        "vlan": None,
    }
    i = 0
    n = len(payload)
    while i + 2 <= n:
        header = struct.unpack("!H", payload[i:i + 2])[0]
        tlv_type = header >> 9
        tlv_len = header & 0x1FF
        i += 2
        value = payload[i:i + tlv_len]
        i += tlv_len
        if len(value) < tlv_len:
            break

        if tlv_type == 0:  # End of LLDPDU
            break
        if tlv_type == 1 and value:
            neighbor["chassis_id"] = _parse_chassis_id(value[0], value[1:])
        elif tlv_type == 2 and value:
            neighbor["port_id"] = _parse_port_id(value[0], value[1:])
        elif tlv_type == 4:
            neighbor["port_description"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 5:
            neighbor["system_name"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 6:
            neighbor["system_description"] = value.decode("utf-8", errors="replace").strip()
        elif tlv_type == 8:
            addr = _parse_management_address(value)
            if addr:
                neighbor["management_ip"] = addr
        elif tlv_type == 127 and len(value) >= 6:
            oui = value[0:3]
            org_subtype = value[3]
            if oui == b"\x00\x80\xc2" and org_subtype == 3:  # 802.1 Port VLAN ID
                neighbor["vlan"] = struct.unpack("!H", value[4:6])[0]

    return neighbor


def _read_exact(stream, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _capture_loop(interface: str) -> None:
    tcpdump = _find_tcpdump()
    if not tcpdump:
        return

    while True:
        proc = None
        try:
            proc = subprocess.Popen(
                [tcpdump, "-i", interface, "-U", "-nn", "-w", "-",
                 "ether", "proto", "0x88cc"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout = proc.stdout
            if _read_exact(stdout, _PCAP_GLOBAL_HEADER_LEN) is None:
                continue

            while True:
                record_header = _read_exact(stdout, _PCAP_RECORD_HEADER_LEN)
                if record_header is None:
                    break
                _, _, incl_len, _ = struct.unpack("<IIII", record_header)
                packet = _read_exact(stdout, incl_len)
                if packet is None or len(packet) < 14:
                    break

                ethertype = struct.unpack("!H", packet[12:14])[0]
                if ethertype != _LLDP_ETHERTYPE:
                    continue

                neighbor = _parse_lldpdu(packet[14:])
                neighbor["last_seen"] = time.time()
                with _lock:
                    _neighbors[interface] = neighbor
        except Exception:
            pass
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        time.sleep(_RESTART_DELAY_SECONDS)


def start_listener(interface: str = "eth0") -> None:
    with _lock:
        if interface in _started_interfaces:
            return
        _started_interfaces.add(interface)
    thread = threading.Thread(target=_capture_loop, args=(interface,), daemon=True)
    thread.start()


def get_neighbor(interface: str = "eth0", stale_after: float = 150.0) -> dict:
    start_listener(interface)

    with _lock:
        neighbor = _neighbors.get(interface)

    if not neighbor:
        return {"interface": interface, "present": False}

    age = time.time() - neighbor["last_seen"]
    if age > stale_after:
        return {"interface": interface, "present": False}

    result = dict(neighbor)
    result["interface"] = interface
    result["present"] = True
    result["age_seconds"] = int(age)
    return result
