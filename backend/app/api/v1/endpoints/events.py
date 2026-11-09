import uuid
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from app.schemas.labops import EventResponse
from app.services.labops_service import service

router = APIRouter()

@router.get("", response_model=List[EventResponse])
async def list_events():
    """List scheduled research seminars, cluster maintenance, and defense workshops"""
    return service.events

@router.post("", response_model=EventResponse)
async def create_event(payload: Dict[str, Any]):
    """Schedule a new laboratory event or maintenance window"""
    title = payload.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="Tiêu đề sự kiện không được để trống")

    new_id = f"ev-{uuid.uuid4().hex[:6]}"
    new_event = {
        "id": new_id,
        "title": title,
        "type": payload.get("type", "SEMINAR"),
        "date": payload.get("date", "2024-11-15"),
        "start_time": payload.get("start_time", "09:00"),
        "end_time": payload.get("end_time", "11:00"),
        "location": payload.get("location", "Phòng Lab"),
        "organizer": payload.get("organizer", "Quản trị viên"),
        "related_server": payload.get("related_server"),
        "status": "SCHEDULED",
        "description": payload.get("description", "")
    }
    service.events.append(new_event)
    return new_event

@router.put("/{event_id}/status")
async def update_event_status(event_id: str, payload: Dict[str, str]):
    """Update event status (SCHEDULED, IN_PROGRESS, COMPLETED, CANCELLED)"""
    status = payload.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Missing status")

    event = next((e for e in service.events if e["id"] == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event["status"] = status
    return {"success": True, "id": event_id, "status": status}

