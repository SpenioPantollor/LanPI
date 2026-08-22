from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.capture import dhcp_monitor, ip_conflict
from backend.network import ap, eth0_mode, link, link_history, wifi

router = APIRouter(prefix="/network")


@router.get("/eth0")
def eth0_link() -> dict:
    return link.get_link_status(TEST_PORT_INTERFACE)


@router.get("/eth0/history")
def eth0_link_history() -> dict:
    return link_history.get_history()


@router.post("/eth0/history/reset")
def eth0_link_history_reset() -> dict:
    return link_history.reset()


@router.get("/ip-conflicts")
def ip_conflicts_get() -> dict:
    return ip_conflict.get_conflicts()


@router.post("/ip-conflicts/reset")
def ip_conflicts_reset() -> dict:
    return ip_conflict.reset()


@router.get("/dhcp-servers")
def dhcp_servers_get() -> dict:
    return dhcp_monitor.get_servers()


@router.post("/dhcp-servers/reset")
def dhcp_servers_reset() -> dict:
    return dhcp_monitor.reset()


class Eth0ModeRequest(BaseModel):
    mode: str
    address: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None


@router.get("/eth0/mode")
def eth0_mode_get() -> dict:
    return eth0_mode.get_mode()


@router.post("/eth0/mode")
def eth0_mode_set(body: Eth0ModeRequest) -> dict:
    if body.mode == "passive":
        return eth0_mode.set_passive()
    if body.mode == "dhcp":
        return eth0_mode.set_dhcp()
    if body.mode == "static":
        return eth0_mode.set_static(body.address or "", body.gateway or "", body.dns or "")
    return {"ok": False, "message": "mode must be passive, dhcp, or static"}


class WifiConnectRequest(BaseModel):
    ssid: str
    password: Optional[str] = None


class WifiForgetRequest(BaseModel):
    name: str


@router.get("/wifi")
def wifi_status() -> dict:
    return wifi.get_status()


@router.get("/wifi/scan")
def wifi_scan() -> list[dict]:
    return wifi.scan()


@router.get("/wifi/saved")
def wifi_saved() -> list[dict]:
    return wifi.list_saved()


@router.post("/wifi/connect")
def wifi_connect(body: WifiConnectRequest) -> dict:
    return wifi.connect(body.ssid, body.password)


@router.post("/wifi/add-known")
def wifi_add_known(body: WifiConnectRequest) -> dict:
    return wifi.add_known(body.ssid, body.password)


@router.post("/wifi/forget")
def wifi_forget(body: WifiForgetRequest) -> dict:
    return wifi.forget(body.name)


@router.post("/wifi/retry-known")
def wifi_retry_known() -> dict:
    return wifi.retry_known()


class ApConfigRequest(BaseModel):
    ssid: str
    password: Optional[str] = None


@router.get("/ap")
def ap_config() -> dict:
    return {"ssid": ap.get_ssid(), "active": ap.is_active(), "address": ap.AP_ADDRESS}


@router.post("/ap")
def ap_set_config(body: ApConfigRequest) -> dict:
    return ap.set_config(body.ssid, body.password)
