from fastapi import APIRouter

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.discovery import cdp, lldp, mndp

router = APIRouter(prefix="/discovery")


@router.get("/lldp")
def lldp_neighbors() -> dict:
    return lldp.get_neighbors(TEST_PORT_INTERFACE)


@router.get("/cdp")
def cdp_neighbors() -> dict:
    return cdp.get_neighbors(TEST_PORT_INTERFACE)


@router.get("/mndp")
def mndp_neighbors() -> dict:
    return mndp.get_neighbors(TEST_PORT_INTERFACE)
