from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.capture import modbus_traffic
from backend.tools import modbus as modbus_tool
from backend.tools import modbus_decode, modbus_poll, modbus_register_scan, modbus_templates, modbus_unit_scan

router = APIRouter(prefix="/tools/modbus")


class ModbusReadRequest(BaseModel):
    host: str
    port: Optional[int] = 502
    unit_id: int
    function_code: int
    address: int
    quantity: int


@router.post("/read")
def modbus_read(body: ModbusReadRequest) -> dict:
    return modbus_tool.read(
        body.host, body.unit_id, body.function_code, body.address,
        body.quantity, body.port or 502,
    )


@router.get("/templates")
def modbus_templates_list() -> list[dict]:
    return modbus_templates.list_templates()


class ModbusTemplateReadRequest(BaseModel):
    template_id: str
    host: str
    port: Optional[int] = 502


@router.post("/templates/read")
def modbus_templates_read(body: ModbusTemplateReadRequest) -> dict:
    return modbus_templates.read_template(body.template_id, body.host, body.port or 502)


class ModbusDeviceIdRequest(BaseModel):
    host: str
    port: Optional[int] = 502
    unit_id: int


@router.post("/device-id")
def modbus_device_id(body: ModbusDeviceIdRequest) -> dict:
    return modbus_tool.read_device_identification(body.host, body.unit_id, body.port or 502)


class ModbusDecodeRequest(BaseModel):
    values: list[int]
    byte_order: Optional[str] = "ABCD"


@router.post("/decode")
def modbus_decode_values(body: ModbusDecodeRequest) -> dict:
    return modbus_decode.decode_registers(body.values, body.byte_order or "ABCD")


class ModbusUnitScanStartRequest(BaseModel):
    host: str
    port: Optional[int] = 502
    start_unit: int
    end_unit: int
    timeout: Optional[float] = 1.0


@router.post("/unit-scan/start")
def modbus_unit_scan_start(body: ModbusUnitScanStartRequest) -> dict:
    return modbus_unit_scan.start(
        body.host, body.start_unit, body.end_unit, body.port or 502, body.timeout or 1.0,
    )


@router.get("/unit-scan/status")
def modbus_unit_scan_status() -> dict:
    return modbus_unit_scan.status()


@router.post("/unit-scan/stop")
def modbus_unit_scan_stop() -> dict:
    return modbus_unit_scan.stop()


class ModbusRegisterScanStartRequest(BaseModel):
    host: str
    port: Optional[int] = 502
    register_type: str
    unit_id: int
    start_address: int
    end_address: int
    timeout: Optional[float] = 1.0


@router.post("/register-scan/start")
def modbus_register_scan_start(body: ModbusRegisterScanStartRequest) -> dict:
    return modbus_register_scan.start(
        body.host, body.register_type, body.unit_id, body.start_address, body.end_address,
        body.port or 502, body.timeout or 1.0,
    )


@router.get("/register-scan/status")
def modbus_register_scan_status() -> dict:
    return modbus_register_scan.status()


@router.post("/register-scan/stop")
def modbus_register_scan_stop() -> dict:
    return modbus_register_scan.stop()


class ModbusPollStartRequest(BaseModel):
    host: str
    port: Optional[int] = 502
    unit_id: int
    function_code: int
    address: int
    quantity: Optional[int] = 1
    interval_ms: Optional[int] = 1000


@router.post("/poll/start")
def modbus_poll_start(body: ModbusPollStartRequest) -> dict:
    return modbus_poll.start(
        body.host, body.port or 502, body.unit_id, body.function_code, body.address,
        body.quantity or 1, body.interval_ms or 1000,
    )


@router.get("/poll/status")
def modbus_poll_status() -> dict:
    return modbus_poll.status()


@router.post("/poll/stop")
def modbus_poll_stop() -> dict:
    return modbus_poll.stop()


@router.get("/traffic")
def modbus_traffic_get() -> dict:
    return modbus_traffic.get_stats()


@router.post("/traffic/reset")
def modbus_traffic_reset() -> dict:
    return modbus_traffic.reset()
