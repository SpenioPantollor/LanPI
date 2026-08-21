from fastapi import APIRouter

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.discovery import cdp, lldp, mndp

router = APIRouter(prefix="/discovery")


@router.get("/lldp")
def lldp_neighbor() -> dict:
    return lldp.get_neighbor(TEST_PORT_INTERFACE)


@router.get("/cdp")
def cdp_neighbor() -> dict:
    return cdp.get_neighbor(TEST_PORT_INTERFACE)


@router.get("/mndp")
def mndp_neighbor() -> dict:
    return mndp.get_neighbor(TEST_PORT_INTERFACE)
