# Feature: Office Admin

## Status
Final

## Version
v1.1

## Summary
Implement an `OfficeAdmin` that supervises office automation tasks using asynchronous, asyncio-based coordination with specialized worker components.

The first supported task is printing calendar events for a selected date.

---

## Problem
The system needs a central coordinator for office tasks that involve multiple asynchronous steps and multiple specialized worker components. Without OfficeAdmin, orchestration leaks into the FastAPI layer, cancellation is difficult, and adding new task types becomes messy.

---

## Goals
1. Provide an `OfficeAdmin` class as the orchestration layer
2. Support asyncio-based message-passing workflows
3. Track task lifecycle and stage per request
4. Coordinate cancellation across workers
5. Support `PRINT_CALENDAR_EVENTS` and `SEND_EMAIL_NOTIFICATIONS` tasks
6. Provide an extensible pattern for future office tasks

---

## Non-Goals
- direct Google Calendar API access
- direct PDF generation
- direct printer integration
- direct Gmail access
- persistent storage (v1 is in-memory only)

---

## Constructor

```python
def __init__(
    self,
    calendar_worker: CalendarWorker,
    document_worker: DocumentWorker,
    printer_worker: PrinterWorker,
    mail_worker: MailWorker,
) -> None
```

Initializes:
- `asyncio.Queue(maxsize=10)` for submitted work items
- in-memory task store (`dict[str, dict]`)
- background `asyncio.Task` running the worker loop
- cancellation tracking per request ID (`dict[str, bool]`)

---

## Public Methods

### `submit_print_calendar_events(selected_date: str) -> str`

Starts a new request to print calendar events for the selected date.

Behavior:
1. if queue is full (`QueueFull`): raise `OfficeAdminQueueFullError`
2. generate UUID4 request ID
3. create status entry:
   - `task_type = "PRINT_CALENDAR_EVENTS"`
   - `status = "PENDING"`, `stage = "PENDING"`
   - `selected_date`, `cancel_requested = False`
   - all counters initialized to 0
4. enqueue work item with `put_nowait`
5. return the request ID immediately

---

### `submit_send_email_notifications(selected_date: str) -> str`

Starts a new request to send email notification drafts for calendar events on the selected date.

Behavior:
1. if queue is full (`QueueFull`): raise `OfficeAdminQueueFullError`
2. generate UUID4 request ID
3. create status entry:
   - `task_type = "SEND_EMAIL_NOTIFICATIONS"`
   - `status = "PENDING"`, `stage = "PENDING"`
   - `selected_date`, `cancel_requested = False`
   - all counters initialized to 0: `emails_expected`, `emails_completed`, `emails_skipped`, `emails_failed`
   - `draft_ids = []`, `skipped_event_ids = []`
4. enqueue work item with `put_nowait`
5. return the request ID immediately

---

### `get_status(request_id: str) -> dict`

Returns the current status record for the request.

Behavior:
- if request exists: return a copy of the status entry
- if not: return `{"status": "UNKNOWN", "request_id": request_id}`

---

### `cancel_request(request_id: str) -> dict`

Requests cancellation of an in-progress task.

Behavior:
1. if request does not exist: return UNKNOWN
2. if task is already terminal (COMPLETED, CANCELLED, ERROR): return current status unchanged
3. set `cancel_requested = True`
4. set `status = CANCEL_REQUESTED`
5. propagate `cancel_request(request_id)` to CalendarWorker, DocumentWorker, PrinterWorker, and MailWorker
6. return the updated status entry

---

### `async shutdown() -> None`

Signals the worker loop to stop and awaits its completion.

Behavior:
1. enqueue `None` sentinel
2. `await` the worker task

---

## Worker Loop

The worker loop runs as a background asyncio.Task:

```python
async def _worker_loop(self):
    while True:
        item = await self._queue.get()
        if item is None:
            break
        await self._process_item(item)
```

### `_process_item` for PRINT_CALENDAR_EVENTS:
1. set `status = RUNNING`, `stage = GETTING_CALENDAR_EVENTS`, update `updated_at`
2. call `calenda_worker.get_events_for_date(self, request_id, selected_date)`
3. return immediately; further processing happens in callbacks

### `_process_item` for SEND_EMAIL_NOTIFICATIONS:
1. set `status = RUNNING`, `stage = GETTING_CALENDAR_EVENTS`, update `updated_at`
2. call `calendar_worker.get_events_for_date(self, request_id, selected_date)`
3. return immediately; further processing happens in callbacks

---

## Callback Methods

All callbacks are `async` methods. They check for terminal status first and discard if already terminal.

### `async calendar_events_complete(request_id, selected_date, events)`

1. discard if task is terminal
2. record `calendar_event_count = len(events)`, `events_retrieved = True`
3. if `cancel_requested`: transition toward CANCELLED (see domain-rules Cancellation Finalization)
4. if `len(events) == 0`: set `status = COMPLETED`, `stage = COMPLETED`; return
5. branch on `task_type`:
   - **PRINT_CALENDAR_EVENTS**: set `stage = CREATING_EVENT_PDFS`, `documents_expected = len(events)`, update `updated_at`; for each event call `document_worker.create_event_document(self, request_id, event)`
   - **SEND_EMAIL_NOTIFICATIONS**: set `stage = CREATING_EMAIL_DRAFTS`, `emails_expected = len(events)`, update `updated_at`; for each event call `mail_worker.create_email_draft(self, request_id, event)`

---

### `async calendar_events_failed(request_id, selected_date, error_text)`

1. discard if task is terminal
2. if `cancel_requested` and `error_text == "Cancelled"`: set `status = CANCELLED`, `stage = CANCELLED`
3. otherwise: set `status = ERROR`, `stage = ERROR`; append error to `errors`; propagate cancel

---

### `async document_complete(request_id, event_id, document_path)`

1. discard if task is terminal
2. increment `documents_completed`; append `document_path` to `document_paths`; update `updated_at`
3. if `documents_completed == documents_expected`:
   - if `cancel_requested`: transition toward CANCELLED
   - otherwise: set `stage = PRINTING_EVENT_PDFS`, `prints_expected = documents_completed`; dispatch print jobs

---

### `async document_failed(request_id, event_id, error_text)`

1. discard if task is terminal
2. increment `documents_failed`; update `updated_at`
3. if `cancel_requested`:
   - if `documents_completed + documents_failed == documents_expected`: set `status = CANCELLED`, `stage = CANCELLED`
4. otherwise:
   - set `status = ERROR`, `stage = ERROR`; append error to `errors`; propagate cancel to all workers

---

### `async print_complete(request_id, event_id, document_path)`

1. discard if task is terminal
2. increment `prints_completed`; update `updated_at`
3. if `prints_completed == prints_expected`: set `status = COMPLETED`, `stage = COMPLETED`

---

### `async print_failed(request_id, event_id, error_text)`

1. discard if task is terminal
2. increment `prints_failed`; update `updated_at`
3. if `cancel_requested`:
   - if `prints_completed + prints_failed == prints_expected`: set `status = CANCELLED`, `stage = CANCELLED`
4. otherwise:
   - set `status = ERROR`, `stage = ERROR`; append error to `errors`; propagate cancel

---

### `async email_draft_complete(request_id, event_id, draft_id)`

1. discard if task is terminal
2. increment `emails_completed`; append `draft_id` to `draft_ids`; update `updated_at`
3. if `emails_completed + emails_skipped + emails_failed == emails_expected`:
   - if `cancel_requested`: set `status = CANCELLED`, `stage = CANCELLED`
   - otherwise: set `status = COMPLETED`, `stage = COMPLETED`

---

### `async email_draft_skipped(request_id, event_id)`

1. discard if task is terminal
2. increment `emails_skipped`; append `event_id` to `skipped_event_ids`; update `updated_at`
3. if `emails_completed + emails_skipped + emails_failed == emails_expected`:
   - if `cancel_requested`: set `status = CANCELLED`, `stage = CANCELLED`
   - otherwise: set `status = COMPLETED`, `stage = COMPLETED`

Note: skipped events are a normal outcome (no `mailto:` found). They count toward the expected total and trigger finalization when all events have been processed.

---

### `async email_draft_failed(request_id, event_id, error_text)`

1. discard if task is terminal
2. increment `emails_failed`; update `updated_at`
3. if `cancel_requested`:
   - if `emails_completed + emails_skipped + emails_failed == emails_expected`: set `status = CANCELLED`, `stage = CANCELLED`
4. otherwise:
   - set `status = ERROR`, `stage = ERROR`; append error to `errors`; propagate cancel to all workers

---

## Status Entry Shape

All task types share the following base fields:

```python
{
    "request_id": str,            # UUID4
    "task_type": str,             # "PRINT_CALENDAR_EVENTS" | "SEND_EMAIL_NOTIFICATIONS"
    "status": str,                # see domain-rules
    "stage": str,                 # see domain-rules
    "selected_date": str,         # ISO 8601 date
    "calendar_event_count": int,
    "events_retrieved": bool,
    "cancel_requested": bool,
    "errors": list[str],
    "created_at": str,            # ISO 8601 datetime
    "updated_at": str,            # ISO 8601 datetime
}
```

Additional fields for `PRINT_CALENDAR_EVENTS`:
```python
{
    "documents_expected": int,
    "documents_completed": int,
    "documents_failed": int,
    "prints_expected": int,
    "prints_completed": int,
    "prints_failed": int,
    "document_paths": list[str],
}
```

Additional fields for `SEND_EMAIL_NOTIFICATIONS`:
```python
{
    "emails_expected": int,
    "emails_completed": int,
    "emails_skipped": int,
    "emails_failed": int,
    "draft_ids": list[str],
    "skipped_event_ids": list[str],
}
```

All numeric counters are initialized to `0`. All list fields are initialized to `[]`.

`make_task_entry` must initialize all fields for the given task type. Unused fields from the other task type are not included.

---

## Error: Queue Full

```python
class OfficeAdminQueueFullError(Exception):
    pass
```

Raised by `submit_print_calendar_events` when the queue is at capacity. FastAPI maps this to HTTP 429.

---

## Acceptance Criteria
1. `submit_print_calendar_events` returns a UUID4 request ID immediately
2. `submit_send_email_notifications` returns a UUID4 request ID immediately
3. initial status is PENDING with all counters at 0
4. stage transitions proceed in correct order for both task types
5. zero-event day completes without further worker dispatch for both task types
6. each callback updates counters correctly
7. `email_draft_skipped` increments `emails_skipped` and counts toward finalization total
8. cancellation at each stage eventually reaches CANCELLED
9. first real failure immediately sets ERROR and propagates cancel
10. late callbacks are silently discarded
11. queue full raises OfficeAdminQueueFullError
12. shutdown completes without hanging
13. `cancel_request` propagates to all workers

---

## Test Plan

### PRINT_CALENDAR_EVENTS
1. submit returns unique UUID4 request ID
2. initial status fields are all correct (task_type, counters, stage = PENDING)
3. stage: PENDING → GETTING_CALENDAR_EVENTS on worker loop pick-up
4. stage: GETTING_CALENDAR_EVENTS → CREATING_EVENT_PDFS on calendar complete
5. stage: CREATING_EVENT_PDFS → PRINTING_EVENT_PDFS when all docs complete
6. stage: PRINTING_EVENT_PDFS → COMPLETED when all prints complete
7. zero-event day: COMPLETED immediately after calendar complete
8. cancel during GETTING_CALENDAR_EVENTS → CANCELLED
9. cancel during CREATING_EVENT_PDFS → all doc failed("Cancelled") → CANCELLED
10. cancel during PRINTING_EVENT_PDFS → all print failed("Cancelled") → CANCELLED
11. calendar failure → ERROR
12. document failure → ERROR, cancel propagated
13. printer failure → ERROR, cancel propagated
14. late callback after ERROR → silently discarded
15. duplicate cancel → idempotent
16. cancel on COMPLETED task → no state change
17. queue full → OfficeAdminQueueFullError
18. shutdown → no hanging tasks

### SEND_EMAIL_NOTIFICATIONS
19. submit returns unique UUID4 request ID with task_type = SEND_EMAIL_NOTIFICATIONS
20. initial status: emails_expected=0, emails_completed=0, emails_skipped=0, emails_failed=0, draft_ids=[], skipped_event_ids=[]
21. stage: PENDING → GETTING_CALENDAR_EVENTS → CREATING_EMAIL_DRAFTS
22. all events complete → COMPLETED
23. all events skipped → COMPLETED (skipped counts toward total)
24. mix of complete and skipped → COMPLETED when all processed
25. zero-event day → COMPLETED immediately (no MailWorker calls)
26. email_draft_complete: increments emails_completed, appends draft_id
27. email_draft_skipped: increments emails_skipped, appends event_id to skipped_event_ids
28. email_draft_failed (real error) → ERROR, cancel propagated
29. cancel during GETTING_CALENDAR_EVENTS → CANCELLED
30. cancel during CREATING_EMAIL_DRAFTS → all email_draft_failed("Cancelled") or skipped counted → CANCELLED when total reached
31. late callback after ERROR → silently discarded
