from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.api.routes._shared import TEST_PORT_INTERFACE
from backend.capture import pcap

router = APIRouter(prefix="/capture")


class CaptureStartRequest(BaseModel):
    duration: Optional[int] = None
    bpf_filter: Optional[str] = None


class CaptureDeleteRequest(BaseModel):
    filename: str


@router.post("/start")
def capture_start(body: CaptureStartRequest) -> dict:
    return pcap.start(TEST_PORT_INTERFACE, body.duration, body.bpf_filter)


@router.get("/status")
def capture_status() -> dict:
    return pcap.status()


@router.post("/stop")
def capture_stop() -> dict:
    return pcap.stop()


@router.get("/list")
def capture_list() -> list[dict]:
    return pcap.list_captures()


@router.post("/delete")
def capture_delete(body: CaptureDeleteRequest) -> dict:
    return pcap.delete_capture(body.filename)


@router.get("/download/{filename}")
def capture_download(filename: str) -> FileResponse:
    path = pcap.get_capture_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="capture not found")
    return FileResponse(path, media_type="application/vnd.tcpdump.pcap", filename=filename)
