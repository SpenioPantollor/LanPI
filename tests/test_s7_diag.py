"""Tests for backend/tools/s7_diag.py: the pure frame-parsing helpers
(no real socket -- these exercise the same field offsets nmap's
s7-info.nse uses, translated from its 1-indexed Lua offsets). Fixture
values match that script's own documented real-world example output
(a Siemens CPU 315-2 DP)."""
from backend.tools import s7_diag


def _frame(length: int, placements: dict[int, bytes], protocol_id: int = 0x32) -> bytes:
    frame = bytearray(b"\x20" * length)  # 0x20 (space) -- harmless, never a null terminator
    if length > 7:
        frame[7] = protocol_id
    for offset, data in placements.items():
        frame[offset:offset + len(data)] = data
    return bytes(frame)


class _FakeSocket:
    """Minimal stand-in respecting the real socket.recv(n) contract --
    never returns more than n bytes, so it exercises _recv_exact()'s
    accumulate-until-count loop the same way a real (possibly
    fragmented) TCP stream would."""

    def __init__(self, response: bytes = b""):
        self._buf = response
        self.sent: list[bytes] = []

    def settimeout(self, t):
        pass

    def setsockopt(self, *a):
        pass

    def bind(self, addr):
        pass

    def connect(self, addr):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, n):
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def close(self):
        pass


def test_recv_tpkt_frame_reads_declared_length():
    payload = b"\x03\x00\x00\x08" + b"\x32\x01\x02\x03"  # TPKT header says 8 bytes total
    assert s7_diag._recv_tpkt_frame(_FakeSocket(payload)) == payload


def test_recv_tpkt_frame_returns_none_on_closed_connection():
    assert s7_diag._recv_tpkt_frame(_FakeSocket(b"")) is None


def test_cstring_reads_null_terminated_text():
    data = b"\x00" * 10 + b"CPU 315-2 DP\x00trailing"
    assert s7_diag._cstring(data, 10) == "CPU 315-2 DP"


def test_cstring_returns_none_past_end_or_without_terminator():
    assert s7_diag._cstring(b"abc", 10) is None
    assert s7_diag._cstring(b"no null here", 0) is None


def test_parse_module_identification_matches_nmap_worked_example():
    frame = _frame(
        130,
        {43: b"6ES7 315-2AG10-0AB0\x00", 71: b"6ES7 315-2AG10-0AB0\x00", 122: bytes([2, 6, 9])},
    )
    result = s7_diag._parse_module_identification(frame)
    assert result["module"] == "6ES7 315-2AG10-0AB0"
    assert result["basic_hardware"] == "6ES7 315-2AG10-0AB0"
    assert result["version"] == "2.6.9"


def test_parse_module_identification_returns_empty_for_wrong_protocol_id():
    frame = _frame(130, {71: b"whatever\x00"}, protocol_id=0x99)
    result = s7_diag._parse_module_identification(frame)
    assert result == {"module": None, "basic_hardware": None, "version": None}


def test_parse_module_identification_returns_empty_when_too_short():
    assert s7_diag._parse_module_identification(_frame(50, {})) == {
        "module": None, "basic_hardware": None, "version": None,
    }


def test_parse_component_identification_matches_nmap_worked_example_no_offset():
    frame = _frame(
        200,
        {
            30: b"\x1c",  # SZL-ID low byte 0x1c -- no +4 offset shift
            39: b"SIMATIC 300(1)\x00",
            73: b"CPU 315-2 DP\x00",
            107: b"\x00",
            141: b"Original Siemens Equipment\x00",
            175: b"S C-X4U421302009\x00",
        },
    )
    result = s7_diag._parse_component_identification(frame)
    assert result["system_name"] == "SIMATIC 300(1)"
    assert result["module_type"] == "CPU 315-2 DP"
    assert result["plant_identification"] is None  # empty string -> None
    assert result["copyright"] == "Original Siemens Equipment"
    assert result["serial_number"] == "S C-X4U421302009"


def test_parse_component_identification_applies_plus_4_offset_when_szl_id_not_0x1c():
    frame = _frame(
        204,
        {
            30: b"\x11",  # SZL-ID low byte != 0x1c -- fields shift by +4
            39 + 4: b"SIMATIC 300(1)\x00",
            73 + 4: b"CPU 315-2 DP\x00",
        },
    )
    result = s7_diag._parse_component_identification(frame)
    assert result["system_name"] == "SIMATIC 300(1)"
    assert result["module_type"] == "CPU 315-2 DP"


def test_open_and_negotiate_cotp_confirms_on_valid_response(monkeypatch):
    fake = _FakeSocket(b"\x03\x00\x00\x06\x11\xd0")  # TPKT + COTP CC (PDU type 0xd0)
    monkeypatch.setattr(s7_diag.socket, "socket", lambda *a, **k: fake)

    sock = s7_diag._open_and_negotiate_cotp("10.0.0.1", 102, "10.0.0.2", 2.0)
    assert sock is fake
    assert fake.sent[0] == s7_diag._COTP_CR_PRIMARY


def test_open_and_negotiate_cotp_returns_none_when_neither_tsap_confirms(monkeypatch):
    sockets = [_FakeSocket(b"\x03\x00\x00\x06\x11\x00"), _FakeSocket(b"\x03\x00\x00\x06\x11\x00")]
    monkeypatch.setattr(s7_diag.socket, "socket", lambda *a, **k: sockets.pop(0))

    assert s7_diag._open_and_negotiate_cotp("10.0.0.1", 102, "10.0.0.2", 2.0) is None
