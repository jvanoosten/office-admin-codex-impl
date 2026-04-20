# Message and Payload Models

This document defines the data shapes used for work items, callbacks, and task status.
See `component-contracts.md` for the callback interface specification.

---

## Work Item Shapes

Work items are `TypedDict` instances enqueued in each component's `asyncio.Queue`.
TypedDict provides the same plain-dict runtime representation but enables static type checking (mypy) across all worker components.
A `None` sentinel value is used to signal worker loop shutdown.

### OfficeAdmin work item
```python
class OfficeAdminWorkItem(TypedDict):
    request_id: str           # UUID4
    task_type: str            # e.g. "PRINT_CALENDAR_EVENTS"
    selected_date: str        # ISO 8601 date string
```

### CalendarWorker work item
```python
class CalendarWorkItem(TypedDict):
    office_admin_ref: Any     # OfficeAdmin reference for callbacks
    request_id: str
    selected_date: str
```

### DocumentWorker work item
```python
class DocumentWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent      # normalized event TypedDict
```

### PrinterWorker work item
```python
class PrinterWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event_id: str
    document_path: str        # absolute path to PDF
```

### MailWorker work item
```python
class MailWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent      # normalized event TypedDict
```

---

## Normalized Calendar Event Shape

```python
class CalendarEvent(TypedDict, total=False):
    id: str                  # Google Calendar event ID; required
    summary: str             # Event title; default "" if absent
    start: str               # ISO 8601 dateTime or date
    end: str                 # ISO 8601 dateTime or date
    timezone: str | None     # IANA timezone from start.timeZone (e.g. "America/Chicago")
    location: str | None
    description: str | None
    html_link: str | None
    status: str | None       # "confirmed", "tentative", "cancelled"
    colorId: str | None      # Google Calendar color ID (e.g. "7"); used by DocumentWorker for header color
```

---

## Task Status Payload Shape

The task status payload is the dict stored in OfficeAdmin's task store and returned by `get_status`.

All task types share these base fields:

```python
{
    # Identity
    "request_id": str,            # UUID4
    "task_type": str,             # "PRINT_CALENDAR_EVENTS" | "SEND_EMAIL_NOTIFICATIONS"

    # Lifecycle
    "status": str,                # PENDING | RUNNING | COMPLETED | CANCEL_REQUESTED | CANCELLED | ERROR
    "stage": str,                 # see domain-rules.md stage values
    "cancel_requested": bool,

    # Input
    "selected_date": str,         # ISO 8601 date string

    # Calendar progress (shared by both task types)
    "calendar_event_count": int,  # total events found; 0 until retrieved
    "events_retrieved": bool,

    # Error tracking
    "errors": list[str],          # error messages from failed callbacks

    # Timestamps (ISO 8601 datetime strings)
    "created_at": str,
    "updated_at": str,
}
```

Additional fields for `PRINT_CALENDAR_EVENTS` tasks:

```python
{
    # Document progress
    "documents_expected": int,    # 0 until calendar complete
    "documents_completed": int,
    "documents_failed": int,
    "document_paths": list[str],  # absolute paths of completed PDFs

    # Print progress
    "prints_expected": int,       # 0 until all documents complete
    "prints_completed": int,
    "prints_failed": int,
}
```

Additional fields for `SEND_EMAIL_NOTIFICATIONS` tasks:

```python
{
    # Email draft progress
    "emails_expected": int,       # 0 until calendar complete
    "emails_completed": int,
    "emails_skipped": int,        # events with no mailto: recipients (normal outcome)
    "emails_failed": int,
    "draft_ids": list[str],       # Gmail API draft IDs of created drafts
    "skipped_event_ids": list[str],  # event IDs of skipped events
}
```

---

## API Response Shapes

### POST /api/office/print-calendar-events — 202
```json
{
  "request_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
}
```

### GET /api/office/status/{request_id} — 200
Full task status payload (see above), serialized to JSON.

### GET /api/office/status/{unknown_id} — 404
```json
{
  "status": "UNKNOWN",
  "request_id": "..."
}
```

### POST /api/office/cancel/{request_id} — 200
Full task status payload after cancellation is recorded.

### Error responses
```json
{"detail": "Invalid date format. Expected YYYY-MM-DD."}       // 400
{"detail": "Server is busy. Try again shortly."}              // 429
{"detail": "Internal server error."}                          // 500
```

---

## Stage Label Map (Frontend)

```javascript
const STAGE_LABELS = {
    "PENDING":                  "Pending",
    "GETTING_CALENDAR_EVENTS":  "Calendar Worker getting calendar events",
    "CREATING_EVENT_PDFS":      "Document Worker creating PDFs",
    "PRINTING_EVENT_PDFS":      "Printer Worker printing PDFs",
    "COMPLETED":                "Completed",
    "CANCELLED":                "Cancelled",
    "ERROR":                    "Error",
};
```
