from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.tools import arp_scan, ip_scanner, port_scanner, tcp_test
from backend.tools import mtr as mtr_tool
from backend.tools import ping as ping_tool

router = APIRouter(prefix="/tools")


class PingStartRequest(BaseModel):
    host: str
    count: Optional[int] = None


@router.post("/ping/start")
def ping_start(body: PingStartRequest) -> dict:
    return ping_tool.start(body.host, body.count)


@router.get("/ping/status")
def ping_status() -> dict:
    return ping_tool.status()


@router.post("/ping/stop")
def ping_stop() -> dict:
    return ping_tool.stop()


class ArpScanRequest(BaseModel):
    network: Optional[str] = None


@router.post("/arp-scan")
def arp_scan_run(body: ArpScanRequest) -> dict:
    return arp_scan.scan(TEST_PORT_INTERFACE, body.network)


class TcpTestRequest(BaseModel):
    host: str
    port: int


@router.post("/tcp-test")
def tcp_test_run(body: TcpTestRequest) -> dict:
    return tcp_test.test_port(body.host, body.port)


class MtrStartRequest(BaseModel):
    host: str
    cycles: Optional[int] = 10


@router.post("/mtr/start")
def mtr_start(body: MtrStartRequest) -> dict:
    return mtr_tool.start(body.host, body.cycles or 10)


@router.get("/mtr/status")
def mtr_status() -> dict:
    return mtr_tool.status()


@router.post("/mtr/stop")
def mtr_stop() -> dict:
    return mtr_tool.stop()


class IpScanRequest(BaseModel):
    target: str


@router.post("/ip-scan/start")
def ip_scan_start(body: IpScanRequest) -> dict:
    return ip_scanner.start(body.target, TEST_PORT_INTERFACE)


@router.get("/ip-scan/status")
def ip_scan_status() -> dict:
    return ip_scanner.status()


@router.post("/ip-scan/stop")
def ip_scan_stop() -> dict:
    return ip_scanner.stop()


class PortScanRequest(BaseModel):
    host: str
    port_range: str


@router.post("/port-scan/start")
def port_scan_start(body: PortScanRequest) -> dict:
    return port_scanner.start(body.host, body.port_range, TEST_PORT_INTERFACE)


@router.get("/port-scan/status")
def port_scan_status() -> dict:
    return port_scanner.status()


@router.post("/port-scan/stop")
def port_scan_stop() -> dict:
    return port_scanner.stop()
