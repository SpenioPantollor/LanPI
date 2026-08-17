"""Passive CDP (Cisco Discovery Protocol) neighbor discovery, via tcpdump.

Same background-thread-plus-cache architecture as lldp.py. CDP frames
are 802.3 + LLC/SNAP (not a plain EtherType frame like LLDP), so the
framing/parsing differs even though the overall approach doesn't:

  [Ethernet: dst(6) src(6) len(2)] [LLC: dsap ssap ctrl] [SNAP: oui(3) pid(2)] [CDP payload]

CDP payload: version(1) ttl(1) checksum(2) then TLVs (type(2) length(2,
includes this 4-byte header) value(length-4)).
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time

_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_CDP_DEST_MAC = "01:00:0c:cc:cc:cc"
_CDP_SNAP_OUI = b"\x00\x00\x0c"
_CDP_SNAP_PID = b"\x20\x00"
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


def _parse_address_tlv(value: bytes) -> str | None:
    """First IPv4 address in a CDP Address(es)/Management-Address(es) TLV."""
    if len(value) < 4:
        return None
    try:
        count = struct.unpack("!I", value[0:4])[0]
    except struct.error:
        return None

    offset = 4
    for _ in range(count):
        if offset + 2 > len(value):
            break
        protocol_type = value[offset]
        protocol_length = value[offset + 1]
        offset += 2
        protocol = value[offset:offset + protocol_length]
        offset += protocol_length
        if offset + 2 > len(value):
            break
        address_length = struct.unpack("!H", value[offset:offset + 2])[0]
        offset += 2
        address = value[offset:offset + address_length]
        offset += address_length

        if protocol_type == 1 and protocol == b"\xcc" and address_length == 4:
            return ".".join(str(b) for b in address)

    return None


def _parse_cdp_payload(payload: bytes) -> dict:
    neighbor = {
        "device_id": None,
        "port_id": None,
        "platform": None,
        "software_version": None,
        "native_vlan": None,
        "address": None,
    }
    if len(payload) < 4:
        return neighbor

    i = 4  # skip version(1) + ttl(1) + checksum(2)
    n = len(payload)
    while i + 4 <= n:
        tlv_type, tlv_len = struct.unpack("!HH", payload[i:i + 4])
        value = payload[i + 4:i + tlv_len]
        if len(value) < tlv_len - 4:
            break
        i += tlv_len if tlv_len >= 4 else n  # malformed length: bail

        if tlv_type == 0x0001:
            neighbor["device_id"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0002:
            addr = _parse_address_tlv(value)
            if addr:
                neighbor["address"] = addr
        elif tlv_type == 0x0003:
            neighbor["port_id"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0005:
            neighbor["software_version"] = value.decode("utf-8", errors="replace").splitlines()[0].strip()
        elif tlv_type == 0x0006:
            neighbor["platform"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x000a and len(value) >= 2:
            neighbor["native_vlan"] = struct.unpack("!H", value[0:2])[0]
        elif tlv_type == 0x0016:
            addr = _parse_address_tlv(value)
            if addr:
                neighbor["address"] = addr

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
                 "ether", "dst", _CDP_DEST_MAC],
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
                if packet is None or len(packet) < 22:
                    continue

                dsap, ssap, control = packet[14], packet[15], packet[16]
                if dsap != 0xAA or ssap != 0xAA or control != 0x03:
                    continue
                oui = packet[17:20]
                pid = packet[20:22]
                if oui != _CDP_SNAP_OUI or pid != _CDP_SNAP_PID:
                    continue

                neighbor = _parse_cdp_payload(packet[22:])
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
