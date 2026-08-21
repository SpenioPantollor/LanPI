"""Every /api/* endpoint, split by feature (v0.2.3 Foundation #5) --
was one 339-line routes.py file, now one submodule per resource
(health, system, network, discovery, tools, modbus, capture, traffic),
each a small APIRouter combined here. Route paths/behavior are
unchanged; this is a file-organization change only.

TEST_PORT_INTERFACE stays re-exported here (from _shared, see that
module's docstring for why it isn't defined directly in this file) so
`from backend.api.routes import TEST_PORT_INTERFACE, router` in
backend/main.py keeps working exactly as it did when this was a single
module.
"""

from fastapi import APIRouter

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.api.routes.capture import router as capture_router
from backend.api.routes.discovery import router as discovery_router
from backend.api.routes.health import router as health_router
from backend.api.routes.modbus import router as modbus_router
from backend.api.routes.network import router as network_router
from backend.api.routes.system import router as system_router
from backend.api.routes.tools import router as tools_router
from backend.api.routes.traffic import router as traffic_router

router = APIRouter()
router.include_router(health_router)
router.include_router(system_router)
router.include_router(network_router)
router.include_router(discovery_router)
router.include_router(tools_router)
router.include_router(modbus_router)
router.include_router(capture_router)
router.include_router(traffic_router)

__all__ = ["TEST_PORT_INTERFACE", "router"]
