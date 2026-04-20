# Component Contracts

This document defines the callback contracts between components.
All callbacks are async methods called directly within the shared asyncio event loop.

---

## OfficeAdmin Callback Methods

These methods are called by workers to report results back to OfficeAdmin.

### `calendar_events_complete(request_id, selected_date, events)`

Called by CalendarWorker when event retrieval succeeds.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `selected_date` | `str` | ISO 8601 date string |
| `events` | `list[dict]` | List of normalized event dicts |

Behavior:
- if task is terminal: discard
- if `cancel_requested`: treat as if cancelled, set CANCELLED if all callbacks received
- if `events` is empty: set `status=COMPLETED`, `stage=COMPLETED`
- otherwise: set `stage=CREATING_EVENT_PDFS`, dispatch document jobs

---

### `calendar_events_failed(request_id, selected_date, error_text)`

Called by CalendarWorker when event retrieval fails or is cancelled.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `selected_date` | `str` | ISO 8601 date string |
| `error_text` | `str` | Error message; `"Cancelled"` if due to cancellation |

Behavior:
- if task is terminal: discard
- if `error_text == "Cancelled"` and `cancel_requested`: set `status=CANCELLED`, `stage=CANCELLED`
- otherwise: set `status=ERROR`, `stage=ERROR`; propagate cancel to all workers

---

### `document_complete(request_id, event_id, document_path)`

Called by DocumentWorker when one PDF is created successfully.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `document_path` | `str` | Absolute path to the generated PDF |

Behavior:
- if task is terminal: discard
- increment `documents_completed`, record `document_path`
- if `documents_completed == documents_expected`: if not cancelled, set `stage=PRINTING_EVENT_PDFS`, dispatch print jobs

---

### `document_failed(request_id, event_id, error_text)`

Called by DocumentWorker when PDF creation fails or is cancelled.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `error_text` | `str` | Error message; `"Cancelled"` if due to cancellation |

Behavior:
- if task is terminal: discard
- increment `documents_failed`
- if `cancel_requested`: check if `(documents_completed + documents_failed) == documents_expected`; if yes, set `status=CANCELLED`, `stage=CANCELLED`
- otherwise (`cancel_requested = False`): set `status=ERROR`, `stage=ERROR`; propagate cancel to all workers; record error

---

### `print_complete(request_id, event_id, document_path)`

Called by PrinterWorker when one print job completes successfully.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `document_path` | `str` | Path of the printed PDF |

Behavior:
- if task is terminal: discard
- increment `prints_completed`
- if `prints_completed == prints_expected`: set `status=COMPLETED`, `stage=COMPLETED`

---

### `print_failed(request_id, event_id, error_text)`

Called by PrinterWorker when a print job fails or is cancelled.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `error_text` | `str` | Error message; `"Cancelled"` if due to cancellation |

Behavior:
- if task is terminal: discard
- increment `prints_failed`
- if `cancel_requested`: check if `(prints_completed + prints_failed) == prints_expected`; if yes, set `status=CANCELLED`, `stage=CANCELLED`
- otherwise: set `status=ERROR`, `stage=ERROR`; propagate cancel; record error

---

### `email_draft_complete(request_id, event_id, draft_id)`

Called by MailWorker when a Gmail draft was created successfully.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `draft_id` | `str` | Gmail API draft ID returned by `drafts().create()` |

Behavior:
- if task is terminal: discard
- increment `emails_completed`; append `draft_id` to `draft_ids`
- if `emails_completed + emails_skipped + emails_failed == emails_expected`: if `cancel_requested`, set CANCELLED; otherwise set COMPLETED

---

### `email_draft_skipped(request_id, event_id)`

Called by MailWorker when an event description contains no email addresses.
This is a **normal outcome**, not an error — events without recipients are skipped.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the skipped calendar event |

Behavior:
- if task is terminal: discard
- increment `emails_skipped`; append `event_id` to `skipped_event_ids`
- if `emails_completed + emails_skipped + emails_failed == emails_expected`: if `cancel_requested`, set CANCELLED; otherwise set COMPLETED
- an all-skipped day (every event skipped) finishes as COMPLETED, not by hanging

---

### `email_draft_failed(request_id, event_id, error_text)`

Called by MailWorker when draft creation fails or the item is cancelled.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `error_text` | `str` | Error message; `"Cancelled"` if due to cancellation |

Behavior:
- if task is terminal: discard
- increment `emails_failed`
- if `cancel_requested`: check if `(emails_completed + emails_skipped + emails_failed) == emails_expected`; if yes, set CANCELLED
- otherwise: set `status=ERROR`, `stage=ERROR`; propagate cancel to all workers; record error

---

## Worker Component Public Methods

These methods are called by OfficeAdmin to send work to workers.

### CalendarWorker

#### `get_events_for_date(office_admin_ref, request_id, selected_date)`

| Parameter | Type | Description |
|---|---|---|
| `office_admin_ref` | `OfficeAdmin` | Reference for callbacks |
| `request_id` | `str` | UUID4 request identifier |
| `selected_date` | `str` | ISO 8601 date string |

Returns: `None` immediately (work is enqueued)

#### `cancel_request(request_id)`

Marks the request as cancelled. Worker checks this flag before processing and before issuing any callback.

#### `async shutdown()`

Enqueues a stop sentinel and awaits the worker task.

---

### DocumentWorker

#### `create_event_document(office_admin_ref, request_id, event)`

| Parameter | Type | Description |
|---|---|---|
| `office_admin_ref` | `OfficeAdmin` | Reference for callbacks |
| `request_id` | `str` | UUID4 request identifier |
| `event` | `dict` | Normalized calendar event |

Returns: `None` immediately (work is enqueued)

#### `cancel_request(request_id)`

Marks the request as cancelled.

#### `async shutdown()`

Enqueues a stop sentinel and awaits the worker task.

---

### PrinterWorker

#### `print_document(office_admin_ref, request_id, event_id, document_path)`

| Parameter | Type | Description |
|---|---|---|
| `office_admin_ref` | `OfficeAdmin` | Reference for callbacks |
| `request_id` | `str` | UUID4 request identifier |
| `event_id` | `str` | Identifier of the calendar event |
| `document_path` | `str` | Absolute path to the PDF to print |

Returns: `None` immediately (work is enqueued)

#### `cancel_request(request_id)`

Marks the request as cancelled.

#### `async shutdown()`

Enqueues a stop sentinel and awaits the worker task.

---

### MailWorker

#### `create_email_draft(office_admin_ref, request_id, event)`

| Parameter | Type | Description |
|---|---|---|
| `office_admin_ref` | `OfficeAdmin` | Reference for callbacks |
| `request_id` | `str` | UUID4 request identifier |
| `event` | `CalendarEvent` | Normalized calendar event |

Returns: `None` immediately (work is enqueued)

Every work item produces exactly one callback: `email_draft_complete`, `email_draft_skipped`, or `email_draft_failed`.

#### `cancel_request(request_id)`

Marks the request as cancelled. Checked before processing and before each callback.

#### `async shutdown()`

Enqueues a stop sentinel and awaits the worker task.

---

## Cancellation Callback Contract Summary

Every work item dispatched to a worker must produce exactly one callback:

| Outcome | Callback called |
|---|---|
| Success | `*_complete` callback |
| Real failure | `*_failed(request_id, ..., error_text)` with descriptive error |
| Cancellation observed | `*_failed(request_id, ..., "Cancelled")` |
| No recipients (MailWorker only) | `email_draft_skipped(request_id, event_id)` — counts toward total, not an error |

Workers must never silently drop a work item.

---

## Normalized Event Structure

```python
class CalendarEvent(TypedDict, total=False):
    id: str            # Google Calendar event ID; required
    summary: str       # Event title; default "" if absent; required
    start: str         # ISO 8601 dateTime or date string
    end: str           # ISO 8601 dateTime or date string
    timezone: str | None  # IANA timezone from start.timeZone (e.g. "America/Chicago")
    location: str | None
    description: str | None
    html_link: str | None
    status: str | None    # "confirmed", "tentative", "cancelled"
    colorId: str | None   # Google Calendar color ID string (e.g. "7"); used by DocumentWorker
```

All fields except `id` and `summary` may be `None` if not present in the Google Calendar response.

---

## Work Item Shapes

### OfficeAdmin work item
```python
class OfficeAdminWorkItem(TypedDict):
    request_id: str
    task_type: str      # "PRINT_CALENDAR_EVENTS" | "SEND_EMAIL_NOTIFICATIONS"
    selected_date: str
```

### CalendarWorker work item
```python
class CalendarWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    selected_date: str
```

### DocumentWorker work item
```python
class DocumentWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent
```

### PrinterWorker work item
```python
class PrinterWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event_id: str
    document_path: str
```

### MailWorker work item
```python
class MailWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent
```
