# Feature: Office Admin FastAPI Backend

## Status
Final

## Version
v1.1

## Summary
Create a FastAPI backend that wires the OfficeAdmin and workers together and exposes endpoints for office automation tasks.

---

## Goals
1. Create and wire OfficeAdmin, CalendarWorker, DocumentWorker, PrinterWorker, and MailWorker via FastAPI lifespan
2. Shut down all workers cleanly on application shutdown (OfficeAdmin first, then workers in reverse)
3. Expose a `POST` endpoint to submit a print-calendar-events request
4. Expose a `POST` endpoint to submit a send-email-notifications request
5. Expose a `GET` endpoint to retrieve request status by request ID
6. Expose a `POST` endpoint to cancel an in-progress request
7. Optionally expose a `GET` endpoint to list active/recent tasks
8. Keep backend routes thin; delegate all orchestration to OfficeAdmin

---

## Non-Goals
- orchestration logic in FastAPI routes
- browser-side business logic
- persistent database support (v1)

---

## Lifespan and Component Wiring

Use FastAPI's `lifespan` context manager (not deprecated `startup`/`shutdown` events):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    calendar_worker = CalendarWorker()
    document_worker = DocumentWorker()
    printer_worker = PrinterWorker()
    mail_worker = MailWorker()
    office_worker = OfficeAdmin(calendar_worker, document_worker, printer_worker, mail_worker)
    app.state.office_worker = office_worker
    yield
    # shutdown: OfficeAdmin first, then workers in reverse startup order
    await office_worker.shutdown()
    await mail_worker.shutdown()
    await printer_worker.shutdown()
    await document_worker.shutdown()
    await calendar_worker.shutdown()
```

Route handlers access `request.app.state.office_admin`.

---

## Required Endpoints

### `POST /api/office/print-calendar-events`

Starts a task to print calendar events for a selected date.

Request body:
```json
{
  "selected_date": "2026-04-10"
}
```

Validation:
- `selected_date` must be a valid ISO 8601 date string (`YYYY-MM-DD`)
- invalid format → HTTP 422 (Pydantic validation error)

Behavior:
1. validate `selected_date`
2. call `office_admin.submit_print_calendar_events(selected_date)`
3. if `OfficeAdminQueueFullError`: return HTTP 429
4. return HTTP 202 with body:
   ```json
   {"request_id": "3f2504e0-..."}
   ```

---

---

### `POST /api/office/send-email-notifications`

Starts a task to create Gmail draft notifications for calendar events on a selected date.

Request body:
```json
{
  "selected_date": "2026-04-11"
}
```

Validation:
- `selected_date` must be a valid ISO 8601 date string (`YYYY-MM-DD`)
- invalid format → HTTP 422 (Pydantic validation error)

Behavior:
1. validate `selected_date`
2. call `office_admin.submit_send_email_notifications(selected_date)`
3. if `OfficeAdminQueueFullError`: return HTTP 429
4. return HTTP 202 with body:
   ```json
   {"request_id": "3f2504e0-..."}
   ```

---


### `GET /api/office/status/{request_id}`

Returns status for a request.

Behavior:
1. call `office_admin.get_status(request_id)`
2. if `status == "UNKNOWN"`: return HTTP 404 with UNKNOWN payload
3. otherwise: return HTTP 200 with the full status entry

---

### `POST /api/office/cancel/{request_id}`

Requests cancellation of an in-progress task.

Behavior:
1. call `office_admin.cancel_request(request_id)`
2. if `status == "UNKNOWN"`: return HTTP 404
3. otherwise: return HTTP 200 with the updated status entry

---

### `GET /api/office/tasks` (optional)

Returns a list of all known tasks.

Behavior:
- return HTTP 200 with a list of status summaries suitable for rendering a task list
- include all tasks regardless of terminal state

---

## Request and Response Models

```python
class PrintCalendarEventsRequest(BaseModel):
    selected_date: date  # Pydantic validates ISO 8601 format

class SendEmailNotificationsRequest(BaseModel):
    selected_date: date  # Pydantic validates ISO 8601 format

class SubmitResponse(BaseModel):
    request_id: str

class StatusResponse(BaseModel):
    request_id: str
    task_type: str
    status: str
    stage: str
    selected_date: str | None
    calendar_event_count: int
    events_retrieved: bool
    cancel_requested: bool
    errors: list[str]
    created_at: str
    updated_at: str
    # PRINT_CALENDAR_EVENTS fields (0/[] when not applicable)
    documents_expected: int
    documents_completed: int
    documents_failed: int
    prints_expected: int
    prints_completed: int
    prints_failed: int
    document_paths: list[str]
    # SEND_EMAIL_NOTIFICATIONS fields (0/[] when not applicable)
    emails_expected: int
    emails_completed: int
    emails_skipped: int
    emails_failed: int
    draft_ids: list[str]
    skipped_event_ids: list[str]
```

Using Pydantic's `date` type for `selected_date` in the request model automatically validates the ISO 8601 format and returns 422 for invalid input. The route may also explicitly return 400 for clarity.

`StatusResponse` is a flat model containing fields for all task types. Fields from the non-applicable task type will be `0` or `[]` as initialized by `make_task_entry`.

---

## Error Responses

| Condition | HTTP Status | Body |
|---|---|---|
| Invalid date format | 422 | Pydantic `ValidationError` response (FastAPI default) |
| Queue full | 429 | `{"detail": "Server is busy. Try again shortly."}` |
| Unknown request ID | 404 | `{"status": "UNKNOWN", "request_id": "..."}` |
| Internal error | 500 | `{"detail": "Internal server error."}` |

---

## Acceptance Criteria
1. application starts successfully with all five workers initialized
2. application shuts down cleanly, awaiting all component shutdown methods in correct order
3. `POST /api/office/print-calendar-events` returns request ID and 202
4. `POST /api/office/send-email-notifications` returns request ID and 202
5. `GET /api/office/status/{id}` returns the current status payload
6. `POST /api/office/cancel/{id}` returns the updated status
7. invalid date returns 422 (Pydantic validation; no custom exception handler needed)
8. queue full returns 429
9. unknown request ID returns 404
10. all status responses include enough detail for UI rendering

---

## Test Plan
1. `POST /api/office/print-calendar-events` with valid date → 202 and request ID
2. `POST /api/office/print-calendar-events` with invalid date → 400
3. `POST /api/office/print-calendar-events` with queue full → 429
4. `POST /api/office/send-email-notifications` with valid date → 202 and request ID
5. `POST /api/office/send-email-notifications` with invalid date → 400
6. `POST /api/office/send-email-notifications` with queue full → 429
7. `GET /api/office/status/{id}` with valid ID → 200 and status payload
8. `GET /api/office/status/{unknown}` → 404 with UNKNOWN status
9. `POST /api/office/cancel/{id}` with valid ID → 200 and updated status
10. `POST /api/office/cancel/{unknown}` → 404
11. routes delegate to OfficeAdmin (not reimplementing logic)
12. lifespan startup creates all five workers; shutdown calls their shutdown methods in correct order
