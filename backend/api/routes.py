import platform
import socket
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.capture import pcap
from backend.discovery import cdp, lldp
from backend.network import ap, eth0_mode, link, wifi
from backend.tools import arp_scan
from backend.tools import ping as ping_tool
from backend.tools import system_info
from backend.tools import tcp_test

router = APIRouter()

TEST_PORT_INTERFACE = "eth0"

# time.monotonic(), not datetime.now(): the Pi has no battery-backed RTC,
# so the wall clock starts each boot at whatever it last saved on shutdown
# and only jumps to the real time once NTP syncs, which happens after this
# module (and the wall clock it would've stamped _START_TIME with) loads.
# A wall-clock-based elapsed time would include that jump -- e.g. boot at
# a stale 23:48, NTP corrects to 16:37 a few minutes later, elapsed
# computed as "16:37 minus 23:48" comes out to ~16.8 hours instead of the
# real ~6 minutes. Monotonic time is immune to clock steps by design.
_START_TIME = time.monotonic()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/status")
def status() -> dict:
    uptime_seconds = time.monotonic() - _START_TIME
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "lanpi_version": "0.1.0",
        "backend_uptime_seconds": int(uptime_seconds),
    }


@router.get("/network/eth0")
def eth0_link() -> dict:
    return link.get_link_status(TEST_PORT_INTERFACE)


@router.get("/system")
def system() -> dict:
    return system_info.get_system_info()


class PingStartRequest(BaseModel):
    host: str
    count: Optional[int] = None


@router.post("/tools/ping/start")
def ping_start(body: PingStartRequest) -> dict:
    return ping_tool.start(body.host, body.count)


@router.get("/tools/ping/status")
def ping_status() -> dict:
    return ping_tool.status()


@router.post("/tools/ping/stop")
def ping_stop() -> dict:
    return ping_tool.stop()


class Eth0ModeRequest(BaseModel):
    mode: str
    address: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None


@router.get("/network/eth0/mode")
def eth0_mode_get() -> dict:
    return eth0_mode.get_mode()


@router.post("/network/eth0/mode")
def eth0_mode_set(body: Eth0ModeRequest) -> dict:
    if body.mode == "passive":
        return eth0_mode.set_passive()
    if body.mode == "dhcp":
        return eth0_mode.set_dhcp()
    if body.mode == "static":
        return eth0_mode.set_static(body.address or "", body.gateway or "", body.dns or "")
    return {"ok": False, "message": "mode must be passive, dhcp, or static"}


@router.get("/discovery/lldp")
def lldp_neighbor() -> dict:
    return lldp.get_neighbor(TEST_PORT_INTERFACE)


@router.get("/discovery/cdp")
def cdp_neighbor() -> dict:
    return cdp.get_neighbor(TEST_PORT_INTERFACE)


class ArpScanRequest(BaseModel):
    network: Optional[str] = None


@router.post("/tools/arp-scan")
def arp_scan_run(body: ArpScanRequest) -> dict:
    return arp_scan.scan(TEST_PORT_INTERFACE, body.network)


class TcpTestRequest(BaseModel):
    host: str
    port: int


@router.post("/tools/tcp-test")
def tcp_test_run(body: TcpTestRequest) -> dict:
    return tcp_test.test_port(body.host, body.port)


class CaptureStartRequest(BaseModel):
    duration: Optional[int] = None
    bpf_filter: Optional[str] = None


class CaptureDeleteRequest(BaseModel):
    filename: str


@router.post("/capture/start")
def capture_start(body: CaptureStartRequest) -> dict:
    return pcap.start(TEST_PORT_INTERFACE, body.duration, body.bpf_filter)


@router.get("/capture/status")
def capture_status() -> dict:
    return pcap.status()


@router.post("/capture/stop")
def capture_stop() -> dict:
    return pcap.stop()


@router.get("/capture/list")
def capture_list() -> list[dict]:
    return pcap.list_captures()


@router.post("/capture/delete")
def capture_delete(body: CaptureDeleteRequest) -> dict:
    return pcap.delete_capture(body.filename)


@router.get("/capture/download/{filename}")
def capture_download(filename: str) -> FileResponse:
    path = pcap.get_capture_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="capture not found")
    return FileResponse(path, media_type="application/vnd.tcpdump.pcap", filename=filename)


class WifiConnectRequest(BaseModel):
    ssid: str
    password: Optional[str] = None


class WifiForgetRequest(BaseModel):
    name: str


@router.get("/network/wifi")
def wifi_status() -> dict:
    return wifi.get_status()


@router.get("/network/wifi/scan")
def wifi_scan() -> list[dict]:
    return wifi.scan()


@router.get("/network/wifi/saved")
def wifi_saved() -> list[dict]:
    return wifi.list_saved()


@router.post("/network/wifi/connect")
def wifi_connect(body: WifiConnectRequest) -> dict:
    return wifi.connect(body.ssid, body.password)


@router.post("/network/wifi/add-known")
def wifi_add_known(body: WifiConnectRequest) -> dict:
    return wifi.add_known(body.ssid, body.password)


@router.post("/network/wifi/forget")
def wifi_forget(body: WifiForgetRequest) -> dict:
    return wifi.forget(body.name)


class ApConfigRequest(BaseModel):
    ssid: str
    password: Optional[str] = None


@router.get("/network/ap")
def ap_config() -> dict:
    return {"ssid": ap.get_ssid(), "active": ap.is_active(), "address": ap.AP_ADDRESS}


@router.post("/network/ap")
def ap_set_config(body: ApConfigRequest) -> dict:
    return ap.set_config(body.ssid, body.password)
