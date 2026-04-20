# Architecture

## Overview
This project implements an asynchronous office automation system centered on an `OfficeAdmin`.

The `OfficeAdmin` supervises office tasks but delegates work to specialized worker components. The first supported workflow is:

1. User requests printing of calendar events for a selected date
2. FastAPI backend submits the request to OfficeAdmin
3. OfficeAdmin creates and tracks a work request, returns a request ID immediately
4. OfficeAdmin dispatches work to CalendarWorker
5. CalendarWorker retrieves events for the requested date from Google Calendar
6. OfficeAdmin dispatches one document-generation request per calendar event to DocumentWorker
7. DocumentWorker creates one PDF per event
8. OfficeAdmin dispatches one print request per generated PDF to PrinterWorker
9. PrinterWorker prints the PDFs
10. UI polls for status and displays the current stage of the request

All admin-to-worker interactions are asynchronous and message-based. OfficeAdmin must remain responsive at all times.

---

## Design Goals
- keep OfficeAdmin responsive at all times
- use asyncio-based asynchronous workflows
- support cancellation of in-progress work
- expose progress and stage updates to the UI
- make it easy to add new office tasks and new worker components
- isolate external system integrations behind specialized components

---

## Concurrency Model

### Runtime: asyncio
All components run within a single asyncio event loop, shared with the FastAPI application.

- each component owns an `asyncio.Queue` for incoming work items
- each component runs a background `asyncio.Task` as its worker loop
- incoming public methods enqueue work items and return immediately
- results are reported by direct async callback calls within the same event loop
- work items are `TypedDict` instances (same runtime representation as plain dicts; enables mypy type checking across all workers)

Because asyncio is cooperative and single-threaded, state mutations that do not span an `await` boundary are safe without explicit locking. All compound check-then-act operations (e.g., incrementing a counter and conditionally dispatching downstream work) must complete before any `await`.

### Blocking Work
Some operations are inherently blocking and must not run directly in the event loop:
- Google Calendar API calls (CalendarWorker) — use `asyncio.get_event_loop().run_in_executor(None, ...)`
- Google OAuth interactive flow (CalendarWorker, first run only) — use `run_in_executor`
- PDF generation (DocumentWorker) — use `run_in_executor`
- Print submission (PrinterWorker) — use `run_in_executor`
- Gmail API calls (MailWorker) — use `run_in_executor`
- Gmail OAuth interactive flow (MailWorker, first run only) — use `run_in_executor`

### OfficeAdmin Work Queue
- OfficeAdmin maintains an `asyncio.Queue(maxsize=10)` for submitted task requests
- if the queue is full when a new task is submitted, the submission is rejected immediately
- the FastAPI backend returns HTTP 429 when the queue is full

### Worker Queue Depth
Worker queues (CalendarWorker, DocumentWorker, PrinterWorker, MailWorker) are unbounded by default in v1.

---

## Shutdown Lifecycle

All components expose an `async shutdown()` method.

Shutdown behavior:
1. enqueue a `None` sentinel to signal the worker loop to stop
2. await the worker task to ensure clean exit

FastAPI must call `shutdown()` on all components during application lifespan teardown. OfficeAdmin shuts down first (draining its queue and stopping dispatch), then worker components in reverse startup order:
1. OfficeAdmin.shutdown()
2. MailWorker.shutdown()
3. PrinterWorker.shutdown()
4. DocumentWorker.shutdown()
5. CalendarWorker.shutdown()

---

## Main Components

### OfficeAdmin
The orchestration component for office automation.

Responsibilities:
- receive task requests
- create unique request IDs (UUID4)
- maintain request status and stage information
- dispatch work to workers
- process worker callbacks
- coordinate cancellation
- support future task types beyond calendar printing

OfficeAdmin owns orchestration, not the implementation details of Google Calendar, PDF generation, or printing.

---

### CalendarWorker
Specialized worker component for reading Google Calendar events.

Responsibilities:
- receive requests for events by date
- authenticate with Google Calendar APIs (inline, on first request)
- retrieve calendar event details for a specific day using local-timezone query boundaries
- filter to timed events only (no all-day events) that start at or after 08:00 and end at or before 18:00 local time (both boundaries inclusive)
- return normalized event data to OfficeAdmin via callback
- support cancellation

Google OAuth is handled inline: on the first request, if no valid token exists, CalendarWorker runs the OAuth flow in an executor (blocking). If OAuth fails, the failure callback is sent to OfficeAdmin. Once a token is obtained and stored, subsequent requests use it. Token refresh is handled transparently by the Google auth library.

CalendarWorker is the only component that talks directly to Google Calendar.

---

### DocumentWorker
Specialized worker component for creating PDF documents.

Responsibilities:
- receive one document-creation request per calendar event
- prune stale PDF files (older than 7 days) from the output directory before each generation
- create a PDF for each event (in executor)
- write PDFs into the project `reports/` directory
- return document paths to OfficeAdmin via callback
- support cancellation

DocumentWorker is the only component that owns PDF layout and generation rules.

---

### PrinterWorker
Specialized worker component for printing documents.

Responsibilities:
- receive print requests for generated PDFs
- send PDFs to the configured printer subsystem (in executor)
- report print completion and failure to OfficeAdmin via callback
- support cancellation

PrinterWorker is the only component that owns print-integration behavior.

---

### MailWorker
Specialized worker component responsible for creating email notification drafts.

Responsibilities:
- receive email-notification work asynchronously
- extract all unique email addresses from the event description text using regex pattern matching (scans entire description, not just `mailto:` links)
- create one Gmail draft per calendar event that has at least one recipient
- include all extracted recipients for that event in the draft
- skip events that do not contain any email addresses in their description (not an error)
- compose draft content from `templates/email_notification_template` (subject on first line; body with `{date}`, `{time}`, `{location}` placeholders)
- report completion, skipped events, and failures back to OfficeAdmin
- support cancellation where practical

Constraints:
- MailWorker is the only component that interacts with Gmail APIs
- MailWorker must not fetch calendar data directly
- MailWorker must not perform orchestration logic
- email content must not be hardcoded; it must be driven by the template file

---

### FastAPI Backend
The backend application exposes API endpoints used by the UI.

Responsibilities:
- create and wire OfficeAdmin and worker components on startup (via lifespan)
- shut down all components on application shutdown (via lifespan)
- accept task requests from the browser
- expose status and cancellation endpoints
- return task progress for display in the UI
- remain thin and delegate all business logic to OfficeAdmin

---

### HTML Frontend
The frontend is a browser-based UI for users.

Responsibilities:
- let the user start supported office tasks
- for Print Calendar Events: show a date picker defaulting to today
- for Send Email Notifications: show a date picker defaulting to tomorrow
- submit selected date to the backend
- show active tasks and their current stages
- allow the user to cancel in-progress tasks

---

## Status and Stage Model

Each task has two tracking fields: `status` (coarse lifecycle) and `stage` (workflow position).

### Status values
| Value | Meaning |
|---|---|
| `PENDING` | Submitted, not yet started |
| `RUNNING` | Actively processing |
| `COMPLETED` | Finished successfully |
| `CANCEL_REQUESTED` | User requested cancellation; propagating to workers |
| `CANCELLED` | All in-flight work has reported back; task is cancelled |
| `ERROR` | A non-cancellation failure occurred |

### Stage values (PRINT_CALENDAR_EVENTS)
| Value | Meaning |
|---|---|
| `PENDING` | Not yet started |
| `GETTING_CALENDAR_EVENTS` | CalendarWorker retrieving events |
| `CREATING_EVENT_PDFS` | DocumentWorker generating PDFs |
| `PRINTING_EVENT_PDFS` | PrinterWorker printing PDFs |
| `COMPLETED` | Workflow finished successfully |
| `CANCELLED` | Workflow cancelled |
| `ERROR` | Workflow failed |

### Stage values (SEND_EMAIL_NOTIFICATIONS)
| Value | Meaning |
|---|---|
| `PENDING` | Not yet started |
| `GETTING_CALENDAR_EVENTS` | CalendarWorker retrieving events |
| `CREATING_EMAIL_DRAFTS` | MailWorker creating Gmail drafts |
| `COMPLETED` | Workflow finished successfully |
| `CANCELLED` | Workflow cancelled |
| `ERROR` | Workflow failed |

### Valid (status, stage) pairs
| status | stage | task types |
|---|---|---|
| PENDING | PENDING | both |
| RUNNING | GETTING_CALENDAR_EVENTS | both |
| RUNNING | CREATING_EVENT_PDFS | PRINT_CALENDAR_EVENTS |
| RUNNING | PRINTING_EVENT_PDFS | PRINT_CALENDAR_EVENTS |
| RUNNING | CREATING_EMAIL_DRAFTS | SEND_EMAIL_NOTIFICATIONS |
| CANCEL_REQUESTED | GETTING_CALENDAR_EVENTS | both |
| CANCEL_REQUESTED | CREATING_EVENT_PDFS | PRINT_CALENDAR_EVENTS |
| CANCEL_REQUESTED | PRINTING_EVENT_PDFS | PRINT_CALENDAR_EVENTS |
| CANCEL_REQUESTED | CREATING_EMAIL_DRAFTS | SEND_EMAIL_NOTIFICATIONS |
| COMPLETED | COMPLETED | both |
| CANCELLED | CANCELLED | both |
| ERROR | ERROR | both |

---

## Cancellation Model

### Requesting cancellation
When `cancel_request(request_id)` is called on OfficeAdmin:
1. OfficeAdmin sets `cancel_requested = True` on the task
2. OfficeAdmin sets `status = CANCEL_REQUESTED`
3. OfficeAdmin propagates `cancel_request(request_id)` to all worker components

### Worker cancellation behavior
Worker components check `cancel_requested` at the start of each work item and before issuing any callback. If cancellation is active, the worker calls the `*_failed` callback with the error text `"Cancelled"` rather than silently dropping the item.

This ensures OfficeAdmin's in-flight counters always reach their expected totals.

### Cancellation finalization in OfficeAdmin
In each failure callback handler, OfficeAdmin checks `cancel_requested`:
- if `cancel_requested = True`: the failure is a cancellation failure, not an error; count it toward the expected total; once all expected callbacks have arrived (completed + failed == expected), set `status = CANCELLED`, `stage = CANCELLED`
- if `cancel_requested = False`: the failure is a real error; see Failure Model below

### Late-arriving callbacks
If a callback arrives for a task whose status is already terminal (COMPLETED, CANCELLED, ERROR), OfficeAdmin discards the callback silently.

---

## Failure Model

### First failure triggers immediate ERROR
When any worker component reports a non-cancellation failure:
1. OfficeAdmin sets `status = ERROR`, `stage = ERROR`
2. OfficeAdmin propagates `cancel_request(request_id)` to all worker components
3. Subsequent callbacks (including cancellation failures from in-flight work) are discarded

This means partial results (some documents generated, some not) are recorded in the status but not acted on.

### Worker crash (v1)
If a worker component's asyncio task dies from an unhandled exception, the exception is logged and the affected task remains in its last known status. Recovery is out of scope for v1.

---

## First Supported Workflow: Print Calendar Events by Date

### User Flow
1. User clicks `Print Calendar Events`
2. Frontend shows a calendar widget, today highlighted by default
3. User selects a date and submits
4. FastAPI posts to `/api/office/print-calendar-events`
5. OfficeAdmin creates request, returns request ID immediately
6. OfficeAdmin updates status to RUNNING, stage to GETTING_CALENDAR_EVENTS, dispatches to CalendarWorker
7. CalendarWorker retrieves events and calls back OfficeAdmin
8. If zero events: OfficeAdmin marks COMPLETED immediately
9. Otherwise: OfficeAdmin updates stage to CREATING_EVENT_PDFS, dispatches N document jobs
10. As each document completes, OfficeAdmin increments counter
11. When all N documents complete: OfficeAdmin updates stage to PRINTING_EVENT_PDFS, dispatches N print jobs
12. As each print completes, OfficeAdmin increments counter
13. When all N prints complete: OfficeAdmin marks COMPLETED
14. Frontend polls `/api/office/status/{request_id}` and renders progress

---

## Workflow: Send Email Notifications

### User Flow
1. user clicks `Send Email Notifications`
2. frontend displays a date picker (default: tomorrow)
3. user selects a date
4. frontend sends request to FastAPI
5. FastAPI calls OfficeAdmin
6. OfficeAdmin creates a `SEND_EMAIL_NOTIFICATIONS` task
7. OfficeAdmin calls CalendarWorker
8. CalendarWorker returns events
9. OfficeAdmin calls MailWorker once per event
10. MailWorker extracts email addresses from event description using regex
11. MailWorker creates Gmail draft if recipients found
12. MailWorker skips events without recipients
13. OfficeAdmin aggregates results
14. UI shows progress and final status

---


## Extensibility Model

### New Task Types
Adding a new task requires:
1. adding a new task type constant
2. adding corresponding stage values
3. adding a `submit_*` method on OfficeAdmin
4. adding callback handlers for the new workflow
5. adding FastAPI endpoints if needed
6. adding frontend UI controls

### New Worker Components
New worker components must:
- follow the same asyncio Queue + Task model
- implement `cancel_request(request_id)` 
- implement `async shutdown()`
- always call a callback for every work item (complete, failed, or cancelled-as-failed)

---

## Data Flow and Ownership

| Component | Owns |
|---|---|
| OfficeAdmin | task lifecycle, request IDs, stage tracking, orchestration rules, cancellation propagation |
| CalendarWorker | Google Calendar auth and retrieval, normalized event extraction |
| DocumentWorker | PDF generation inputs/outputs, document file paths |
| PrinterWorker | print-submission tracking, printer-specific execution state |
| MailWorker | Gmail auth and draft creation, recipient extraction from event descriptions |
| FastAPI | HTTP routing, input validation, component wiring, lifecycle |
| Frontend | user interaction, polling, stage display |

---

## Architectural Boundaries

### OfficeAdmin must not:
- directly call Google Calendar APIs
- directly create PDFs
- directly interact with the printer subsystem

### CalendarWorker must not:
- manage OfficeAdmin task lifecycle rules

### DocumentWorker must not:
- decide which tasks to run next
- manage Google Calendar retrieval or printer operations

### PrinterWorker must not:
- generate documents or fetch calendar events

### MailWorker must not:
- fetch calendar data directly
- perform orchestration logic
- hardcode email content (must be driven by the template file)

### FastAPI must not:
- implement orchestration logic or duplicate worker logic

---

## Task Store
- in-memory only in v1
- all task state is lost on server restart
- this is documented behavior; clients should not rely on task history persisting across restarts
- tasks are automatically removed from the store **30 minutes** after reaching a terminal state (COMPLETED, CANCELLED, ERROR) using an asyncio cleanup task scheduled at transition time
- worker `cancelled` flags are removed **60 minutes** after `cancel_request` is called, to bound memory growth in long-running sessions

---

## Suggested Project Structure

```text
Docs/
Specs/
src/
  office_worker.py
  calendar_worker.py
  document_worker.py
  printer_worker.py
  mail_worker.py
  api.py
  models.py
  message_types.py
  task_store.py
templates/
  index.html
  email_notification_template
static/
  app.js
  styles.css
reports/
tests/
```

---

## Future Improvements
- WebSocket/SSE push updates instead of polling
- persistent task store (database-backed)
- retry policies for worker failures
- per-user timezone support for calendar queries
- printer selection
- worker crash watchdog and task recovery
- multi-user task ownership
- horizontal scaling (requires replacing in-process queues)
