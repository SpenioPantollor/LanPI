from fastapi import APIRouter

from backend.tools import system_info

router = APIRouter()


@router.get("/system")
def system() -> dict:
    return system_info.get_system_info()
