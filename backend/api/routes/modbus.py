from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.tools import modbus as modbus_tool
from backend.tools import modbus_templates

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
