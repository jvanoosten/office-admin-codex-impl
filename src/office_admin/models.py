from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

PRINT_CALENDAR_EVENTS = "PRINT_CALENDAR_EVENTS"
SEND_EMAIL_NOTIFICATIONS = "SEND_EMAIL_NOTIFICATIONS"

PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCELLED = "CANCELLED"
ERROR = "ERROR"
UNKNOWN = "UNKNOWN"

GETTING_CALENDAR_EVENTS = "GETTING_CALENDAR_EVENTS"
CREATING_EVENT_PDFS = "CREATING_EVENT_PDFS"
PRINTING_EVENT_PDFS = "PRINTING_EVENT_PDFS"
CREATING_EMAIL_DRAFTS = "CREATING_EMAIL_DRAFTS"

TERMINAL_STATUSES = {COMPLETED, CANCELLED, ERROR}
PRINT_TASK_TYPES = {PRINT_CALENDAR_EVENTS}


class CalendarEvent(TypedDict, total=False):
    id: str
    summary: str
    start: str
    end: str
    timezone: str | None
    location: str | None
    description: str | None
    html_link: str | None
    status: str | None
    colorId: str | None


class OfficeAdminWorkItem(TypedDict):
    request_id: str
    task_type: Literal["PRINT_CALENDAR_EVENTS", "SEND_EMAIL_NOTIFICATIONS"]
    selected_date: str


class DocumentWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_task_entry(request_id: str, task_type: str, selected_date: str) -> dict[str, Any]:
    timestamp = utc_now_iso()
    return {
        "request_id": request_id,
        "task_type": task_type,
        "status": PENDING,
        "stage": PENDING,
        "selected_date": selected_date,
        "calendar_event_count": 0,
        "events_retrieved": False,
        "cancel_requested": False,
        "errors": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "documents_expected": 0,
        "documents_completed": 0,
        "documents_failed": 0,
        "prints_expected": 0,
        "prints_completed": 0,
        "prints_failed": 0,
        "document_paths": [],
        "emails_expected": 0,
        "emails_completed": 0,
        "emails_skipped": 0,
        "emails_failed": 0,
        "draft_ids": [],
        "skipped_event_ids": [],
        "calendar_events": [],
    }
