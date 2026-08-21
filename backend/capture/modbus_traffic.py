"""Passive Modbus TCP traffic analysis (Modbus expansion #9).

Watches the shared packet-capture dispatcher (backend/capture/
dispatcher.py) for Modbus TCP traffic (conventionally TCP/502) instead
of running a separate tcpdump listener -- the whole point of the
dispatcher consolidation (v0.2.3 Foundation #3) was exactly this: one
capture, many consumers.

Tracks communication relationships keyed by (client_ip, server_ip,
unit_id, function_code): request/response/exception counts and
response-time stats -- useful for spotting an overloaded server, a
device answering exceptions to most requests, or more than one master
talking to the same slave (multiple client IPs against the same
server_ip is visible directly in the relationship table, no extra
logic needed).

Known limitation -- read before trusting "missing" counts: this
inspects individual captured packets, it does NOT perform TCP stream
reassembly. A Modbus PDU split across TCP segments won't parse
(silently skipped -- undercounting, not misreading), and a request
whose response genuinely wasn't captured (rather than never sent)
looks identical to a real non-response. Correlation uses the Modbus
TCP Transaction ID plus the connection's client/server IPs, which
reliably matches a captured response to its captured request, but
can't prove a response never existed if this capture simply missed it
-- see the module docstring notes on dispatcher.py for the same
"single packet, no reassembly" trade-off other listeners accepted.
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_MODBUS_PORT = 502
_PENDING_TIMEOUT_SECONDS = 5.0  # how long an unanswered request waits before counting as "missing"
_MAX_RELATIONSHIPS = 200

_lock = threading.Lock()
_relationships: dict[tuple, dict] = {}  # (client_ip, server_ip, unit_id, function_code) -> stats
_pending: dict[tuple, dict] = {}  # (client_ip, server_ip, transaction_id) -> {sent_at, unit_id, function_code}
_started = False


def _empty_relationship() -> dict:
    return {
        "requests": 0,
        "responses": 0,
        "exceptions": 0,
        "missing": 0,
        "last_seen": None,
        "_sum_ms": 0.0,
        "_ms_count": 0,
        "min_ms": None,
        "avg_ms": None,
        "max_ms": None,
    }


def _parse_ipv4_tcp(packet: bytes):
    """Returns (src_ip, dst_ip, src_port, dst_port, tcp_payload), or
    None if this isn't a parseable IPv4/TCP packet."""
    if len(packet) < 14 + 20:
        return None
    ethertype = struct.unpack("!H", packet[12:14])[0]
    if ethertype != 0x0800:
        return None
    ihl = (packet[14] & 0x0F) * 4
    proto = packet[23]
    if proto != 6:  # TCP
        return None
    if len(packet) < 14 + ihl + 20:
        return None
    src_ip = ".".join(str(b) for b in packet[26:30])
    dst_ip = ".".join(str(b) for b in packet[30:34])
    tcp_offset = 14 + ihl
    src_port, dst_port = struct.unpack("!HH", packet[tcp_offset:tcp_offset + 4])
    data_offset = ((packet[tcp_offset + 12] >> 4) & 0x0F) * 4
    payload_offset = tcp_offset + data_offset
    if len(packet) < payload_offset:
        return None
    return src_ip, dst_ip, src_port, dst_port, packet[payload_offset:]


def _parse_mbap_pdu(payload: bytes):
    """Returns (transaction_id, unit_id, function_code, is_exception,
    exception_code), or None if `payload` isn't a parseable single
    Modbus MBAP+PDU (see module docstring: no TCP reassembly, and only
    the first message in a pipelined packet is seen)."""
    if len(payload) < 8:
        return None
    transaction_id, protocol_id, length, unit_id = struct.unpack("!HHHB", payload[:7])
    if protocol_id != 0:
        return None
    pdu = payload[7:7 + (length - 1)]
    if len(pdu) < 1:
        return None
    function_code = pdu[0]
    is_exception = bool(function_code & 0x80)
    exception_code = pdu[1] if is_exception and len(pdu) > 1 else None
    return transaction_id, unit_id, (function_code & 0x7F), is_exception, exception_code


def _prune_stale_pending(now: float) -> None:
    stale_keys = [key for key, entry in _pending.items() if now - entry["sent_at"] > _PENDING_TIMEOUT_SECONDS]
    for key in stale_keys:
        entry = _pending.pop(key)
        client_ip, server_ip, _transaction_id = key
        rel = _relationships.get((client_ip, server_ip, entry["unit_id"], entry["function_code"]))
        if rel is not None:
            rel["missing"] += 1


def handle_packet(packet: bytes) -> None:
    parsed = _parse_ipv4_tcp(packet)
    if parsed is None:
        return
    src_ip, dst_ip, src_port, dst_port, payload = parsed
    if src_port != _MODBUS_PORT and dst_port != _MODBUS_PORT:
        return

    parsed_mbap = _parse_mbap_pdu(payload)
    if parsed_mbap is None:
        return
    transaction_id, unit_id, function_code, is_exception, _exception_code = parsed_mbap
    now = time.time()

    with _lock:
        _prune_stale_pending(now)

        if dst_port == _MODBUS_PORT:
            client_ip, server_ip = src_ip, dst_ip
            rel_key = (client_ip, server_ip, unit_id, function_code)
            rel = _relationships.get(rel_key)
            if rel is None:
                if len(_relationships) >= _MAX_RELATIONSHIPS:
                    return
                rel = _empty_relationship()
                _relationships[rel_key] = rel
            rel["requests"] += 1
            rel["last_seen"] = now
            _pending[(client_ip, server_ip, transaction_id)] = {
                "sent_at": now, "unit_id": unit_id, "function_code": function_code,
            }
        else:
            server_ip, client_ip = src_ip, dst_ip
            pending_entry = _pending.pop((client_ip, server_ip, transaction_id), None)
            rel = _relationships.get((client_ip, server_ip, unit_id, function_code))
            if rel is None:
                return  # a response with no tracked request to attribute it to
            rel["responses"] += 1
            rel["last_seen"] = now
            if is_exception:
                rel["exceptions"] += 1
            if pending_entry is not None:
                response_ms = round((now - pending_entry["sent_at"]) * 1000, 1)
                rel["_sum_ms"] += response_ms
                rel["_ms_count"] += 1
                rel["min_ms"] = response_ms if rel["min_ms"] is None else min(rel["min_ms"], response_ms)
                rel["max_ms"] = response_ms if rel["max_ms"] is None else max(rel["max_ms"], response_ms)
                rel["avg_ms"] = round(rel["_sum_ms"] / rel["_ms_count"], 1)


def start_listener(interface: str = "eth0") -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    dispatcher.start_listener(interface)
    dispatcher.register_handler(handle_packet)


def get_stats() -> dict:
    with _lock:
        _prune_stale_pending(time.time())
        relationships = [
            {
                "client_ip": client_ip,
                "server_ip": server_ip,
                "unit_id": unit_id,
                "function_code": function_code,
                "requests": rel["requests"],
                "responses": rel["responses"],
                "exceptions": rel["exceptions"],
                "missing": rel["missing"],
                "min_ms": rel["min_ms"],
                "avg_ms": rel["avg_ms"],
                "max_ms": rel["max_ms"],
                "last_seen": rel["last_seen"],
            }
            for (client_ip, server_ip, unit_id, function_code), rel in _relationships.items()
        ]
        relationships.sort(key=lambda r: r["requests"], reverse=True)
        return {"relationships": relationships}


def reset() -> dict:
    with _lock:
        _relationships.clear()
        _pending.clear()
    return {"ok": True, "message": "Modbus traffic stats reset"}
