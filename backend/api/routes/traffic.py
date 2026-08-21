from fastapi import APIRouter

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.capture import traffic_stats

router = APIRouter(prefix="/traffic")


@router.get("/stats")
def traffic_stats_get() -> dict:
    traffic_stats.start_listener(TEST_PORT_INTERFACE)
    return traffic_stats.get_stats()


@router.post("/reset")
def traffic_stats_reset() -> dict:
    return traffic_stats.reset()
