"""Tests for backend/tools/s7_diag.py: the pure frame-parsing helpers
(no real socket -- these exercise the same field offsets nmap's
s7-info.nse uses, translated from its 1-indexed Lua offsets). Fixture
values match that script's own documented real-world example output
(a Siemens CPU 315-2 DP)."""
import struct

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


# Real "Object does not exist" response captured live 2026-08-25 from a
# real Siemens S7-1200 (CPU 1214C) -- it doesn't implement SZL 0x001c
# (component identification), unlike the older S7-300 nmap's own
# worked example is based on. Return code 0x0a, per the standard
# S7comm DataItem return-code table.
_REAL_SZL_NOT_SUPPORTED_FRAME = bytes.fromhex(
    "0300002102f080320700000000000c000400011208128401010000d4010a000000"
)


def test_szl_return_code_reads_real_not_supported_response():
    assert s7_diag._szl_return_code(_REAL_SZL_NOT_SUPPORTED_FRAME) == 0x0A


def test_szl_failure_message_describes_real_not_supported_response():
    message = s7_diag._szl_failure_message(_REAL_SZL_NOT_SUPPORTED_FRAME)
    assert message == "object does not exist -- SZL not implemented by this CPU"


def test_szl_failure_message_is_none_on_success_return_code():
    frame = bytearray(_REAL_SZL_NOT_SUPPORTED_FRAME)
    data_offset = 17 + 12  # header(17) + this frame's own param_len(12)
    frame[data_offset] = s7_diag._SZL_RETURN_CODE_SUCCESS
    assert s7_diag._szl_failure_message(bytes(frame)) is None


def test_parse_component_identification_leaves_all_none_on_real_not_supported_response():
    # The parser must degrade safely (no crash, no garbage strings)
    # on this real short/error response -- the explicit error message
    # comes from _szl_failure_message(), a separate concern.
    result = s7_diag._parse_component_identification(_REAL_SZL_NOT_SUPPORTED_FRAME)
    assert all(v is None for v in result.values())


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


def test_open_and_negotiate_cotp_falls_back_after_connection_reset_on_first_tsap(monkeypatch):
    # Real-world case confirmed live 2026-08-25 against an actual
    # S7-1200: it hard-resets the TCP connection when it doesn't like
    # the first TSAP, rather than a clean COTP-level non-confirm --
    # the second TSAP, on a fresh connection, must still get tried.
    class ResettingSocket(_FakeSocket):
        def sendall(self, data):
            raise ConnectionResetError("Connection reset by peer")

    fallback = _FakeSocket(b"\x03\x00\x00\x06\x11\xd0")
    sockets = [ResettingSocket(), fallback]
    monkeypatch.setattr(s7_diag.socket, "socket", lambda *a, **k: sockets.pop(0))

    sock = s7_diag._open_and_negotiate_cotp("10.0.0.1", 102, "10.0.0.2", 2.0)
    assert sock is fallback
    assert fallback.sent[0] == s7_diag._COTP_CR_FALLBACK


def test_open_and_negotiate_cotp_reraises_when_every_attempt_errors(monkeypatch):
    class RefusingSocket(_FakeSocket):
        def connect(self, addr):
            raise ConnectionRefusedError("refused")

    monkeypatch.setattr(s7_diag.socket, "socket", lambda *a, **k: RefusingSocket())

    try:
        s7_diag._open_and_negotiate_cotp("10.0.0.1", 102, "10.0.0.2", 2.0)
        assert False, "expected ConnectionRefusedError to propagate"
    except ConnectionRefusedError:
        pass


# Read Var (S7 process-data tag reads) ---------------------------------

# Hand-verified against a real-world worked example: reading DB1, byte
# offset 4, as a single BYTE. Every field (transport size 0x02=BYTE,
# count=1, DB number=1, area=0x84, address=0x000020 == 4<<3) matches
# that example byte-for-byte.
def test_build_read_var_request_matches_known_worked_example():
    request = s7_diag._build_read_var_request(0x84, 1, 4, 0, "BYTE")
    assert request == bytes.fromhex(
        "0300001f02f080320100000001000e00000401120a10020001000184000020"
    )


def test_build_read_var_request_bit_uses_bit_transport_size_and_address():
    # M0.3 -- area M, byte offset 0, bit offset 3: transport size 0x01
    # (BIT), count 1, address = 0*8+3 = 3.
    request = s7_diag._build_read_var_request(0x83, 0, 0, 3, "BIT")
    item = request[17 + 2:]  # after TPKT+COTP+header(17) + param header(2)
    assert item[3] == 0x01  # transport size = BIT
    assert item[8] == 0x83  # area = M
    assert item[9:12] == bytes([0, 0, 3])  # address = bit 3


def test_build_read_var_request_word_doubles_count_for_byte_size():
    # A WORD is 2 bytes -- transport size collapses to BYTE (0x02) with
    # count=2, per the convention every S7 client uses.
    request = s7_diag._build_read_var_request(0x84, 5, 10, 0, "WORD")
    item = request[17 + 2:]
    assert item[3] == 0x02  # transport size = BYTE
    assert item[4:6] == bytes([0, 2])  # count = 2 bytes
    assert item[9:12] == bytes([0, 0, 10 * 8])  # address = byte 10 << 3


def _read_var_ack_frame(return_code: int, data: bytes, error_class: int = 0, error_code: int = 0) -> bytes:
    """Builds a minimal Ack_Data (rosctr=0x03) Read Var response frame:
    the normal 10-byte header, PLUS the 2 extra header bytes
    (error_class/error_code) that Ack/Ack_Data PDUs carry and Job/
    UserData PDUs don't -- then one item: return_code +
    transport_size(0x04) + length(bits) + data."""
    item_data = bytes([return_code, 0x04]) + struct.pack("!H", len(data) * 8) + data
    parameter = bytes([0x04, 0x01])  # function=Read Var, item count=1
    header = struct.pack(
        "!BBHHHHBB", 0x32, 0x03, 0x0000, 0x0001, len(parameter), len(item_data), error_class, error_code
    )
    cotp = bytes([0x02, 0xF0, 0x80])
    s7_pdu = header + parameter + item_data
    tpkt = struct.pack("!BBH", 0x03, 0x00, 4 + len(cotp) + len(s7_pdu))
    return tpkt + cotp + s7_pdu


def test_parse_read_var_response_returns_data_on_success():
    frame = _read_var_ack_frame(0xFF, b"\x2a")
    data, error = s7_diag._parse_read_var_response(frame, 1)
    assert data == b"\x2a"
    assert error is None


def test_parse_read_var_response_returns_message_on_failure_code():
    frame = _read_var_ack_frame(0x05, b"")
    data, error = s7_diag._parse_read_var_response(frame, 1)
    assert data is None
    assert error == "invalid address"


def test_parse_read_var_response_reports_short_response():
    data, error = s7_diag._parse_read_var_response(b"\x03\x00\x00\x04", 1)
    assert data is None
    assert error == "invalid or no response"


def test_parse_read_var_response_reports_pdu_level_error():
    # error_class/error_code set (non-zero) means the whole PDU was
    # rejected before any per-item return code was even produced.
    frame = _read_var_ack_frame(0xFF, b"\x2a", error_class=0x81, error_code=0x04)
    data, error = s7_diag._parse_read_var_response(frame, 1)
    assert data is None
    assert error == "S7 PDU error (class 0x81, code 0x04)"


def test_decode_tag_value_bit():
    assert s7_diag._decode_tag_value("BIT", b"\x01") is True
    assert s7_diag._decode_tag_value("BIT", b"\x00") is False


def test_decode_tag_value_byte():
    assert s7_diag._decode_tag_value("BYTE", b"\xff") == 255


def test_decode_tag_value_word_and_int():
    assert s7_diag._decode_tag_value("WORD", b"\xff\xff") == 65535
    assert s7_diag._decode_tag_value("INT", b"\xff\xff") == -1


def test_decode_tag_value_dword_and_dint():
    assert s7_diag._decode_tag_value("DWORD", b"\xff\xff\xff\xff") == 4294967295
    assert s7_diag._decode_tag_value("DINT", b"\xff\xff\xff\xff") == -1


def test_decode_tag_value_real():
    raw = struct.pack("!f", 3.5)
    assert s7_diag._decode_tag_value("REAL", raw) == 3.5


def test_read_tag_rejects_missing_host():
    result = s7_diag.read_tag("", "DB", 0, "BYTE")
    assert result == {"ok": False, "message": "host is required"}


def test_read_tag_rejects_unknown_area():
    result = s7_diag.read_tag("10.0.0.1", "X", 0, "BYTE")
    assert result["ok"] is False
    assert "area must be one of" in result["message"]


def test_read_tag_rejects_unknown_type():
    result = s7_diag.read_tag("10.0.0.1", "DB", 0, "FLOAT")
    assert result["ok"] is False
    assert "type must be one of" in result["message"]


def test_read_tag_requires_db_number_for_db_area():
    result = s7_diag.read_tag("10.0.0.1", "DB", 0, "BYTE", db_number=0)
    assert result["ok"] is False
    assert "db_number is required" in result["message"]


def test_read_tag_rejects_out_of_range_bit_offset():
    result = s7_diag.read_tag("10.0.0.1", "M", 0, "BIT", bit_offset=8)
    assert result["ok"] is False
    assert "bit_offset must be 0-7" in result["message"]
