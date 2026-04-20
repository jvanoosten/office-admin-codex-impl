# Testing Strategy

## Goals
Tests should validate behavior and workflow, not just implementation details.

The project should prefer:
- fast unit tests
- isolated orchestration tests using fake worker components
- API tests with mocked OfficeAdmin
- targeted integration tests
- manual tests only where external auth, printer behavior, or browser rendering make automation impractical

---

## Core Testing Principles
1. Automated tests must not depend on live external services
2. Automated tests must not require real Google OAuth interaction
3. Automated tests must not require access to a real printer
4. Office Admin orchestration must be testable with fake worker components
5. Cancellation behavior must be tested explicitly
6. Stage transitions must be tested explicitly
7. Tests must be deterministic: fake components must call callbacks synchronously or at controlled points
8. Tests must not leave dangling asyncio tasks after completion

---

## asyncio Testing Approach

All components use `asyncio.Queue` and `asyncio.Task`. Tests must be written using `pytest-asyncio` with `asyncio_mode = "auto"`.

### Fake worker components
Fake CalendarWorker, DocumentWorker, and PrinterWorker should:
- capture enqueued work items for inspection
- expose methods to manually trigger callbacks (for controlled orchestration testing)
- support simulating both success and failure callbacks
- support simulating cancellation callbacks (`*_failed` with `"Cancelled"`)

This allows tests to drive the workflow step by step without running real worker loops.

### Testing executor-based blocking work
Worker components run blocking I/O in `run_in_executor`. Tests should mock the executor target (e.g., the Google Calendar service, the PDF library call, the print command) rather than mocking `run_in_executor` itself.

### Shutdown in tests
Every test that creates an component must call `await component.shutdown()` in a teardown fixture. This prevents dangling tasks from interfering with other tests.

---

## Test Categories

### Unit Tests
Cover:
- OfficeAdmin request creation and status tracking
- OfficeAdmin stage transitions
- OfficeAdmin dispatch logic
- OfficeAdmin cancellation propagation
- OfficeAdmin failure handling (cancel_requested vs real error)
- CalendarWorker event normalization and date-range logic
- CalendarWorker cancellation callback behavior
- DocumentWorker file naming logic
- DocumentWorker cancellation callback behavior
- PrinterWorker cancellation callback behavior
- helper functions for IDs, naming, and status updates

### Orchestration Tests
These tests exercise the full callback workflow using fake worker components.

Required scenarios for PRINT_CALENDAR_EVENTS:
- happy path: submit → calendar → N documents → N prints → COMPLETED
- zero-event day: submit → calendar returns empty list → COMPLETED immediately
- calendar failure: `calendar_events_failed` with real error → ERROR
- document failure: one `document_failed` (real error) → ERROR; remaining in-flight work cancelled
- printer failure: one `print_failed` (real error) → ERROR
- cancellation during GETTING_CALENDAR_EVENTS: `calendar_events_failed("Cancelled")` → CANCELLED
- cancellation during CREATING_EVENT_PDFS: some `document_failed("Cancelled")` arrive after cancel → CANCELLED once all expected callbacks received
- cancellation during PRINTING_EVENT_PDFS: print jobs cancelled → CANCELLED
- late callback discarded: callback arrives after task is terminal → silently ignored
- duplicate cancel: cancel called twice → idempotent, no state corruption
- cancel on completed task: cancel request on already-COMPLETED task → no state change

Required scenarios for SEND_EMAIL_NOTIFICATIONS:
- happy path: submit → calendar → N email_draft_complete → COMPLETED
- all-skipped: submit → calendar → all email_draft_skipped → COMPLETED
- mixed complete/skipped: some complete, some skipped → COMPLETED when all processed
- zero-event day: calendar returns empty list → COMPLETED immediately (no MailWorker calls)
- email failure (real error): one `email_draft_failed` (real error) → ERROR; remaining cancelled
- cancellation during GETTING_CALENDAR_EVENTS: `calendar_events_failed("Cancelled")` → CANCELLED
- cancellation during CREATING_EMAIL_DRAFTS: mix of failed("Cancelled") and skipped counted → CANCELLED when total reached

### Cancellation Counter Tests
Explicitly test the finalization logic:
- `documents_completed + documents_failed == documents_expected` triggers CANCELLED when `cancel_requested = True`
- `emails_completed + emails_skipped + emails_failed == emails_expected` triggers CANCELLED when `cancel_requested = True`
- `emails_skipped` counts toward the finalization total (all-skipped day must complete, not hang)
- all worker components' cancellation failure callbacks are counted correctly
- partial completion (some complete, some cancelled) correctly sets counters

### API Tests
Cover:
- `POST /api/office/print-calendar-events` success — returns request ID and 202
- `POST /api/office/print-calendar-events` invalid date — returns 400
- `POST /api/office/print-calendar-events` queue full — returns 429
- `POST /api/office/send-email-notifications` success — returns request ID and 202
- `POST /api/office/send-email-notifications` invalid date — returns 400
- `POST /api/office/send-email-notifications` queue full — returns 429
- `GET /api/office/status/{request_id}` success — returns status payload
- `GET /api/office/status/{unknown_id}` — returns UNKNOWN status
- `POST /api/office/cancel/{request_id}` success — returns updated status
- `POST /api/office/cancel/{unknown_id}` — returns appropriate error
- status payload shape includes all required fields for UI rendering

API tests must mock OfficeAdmin rather than exercise live worker components.

### Frontend Manual Tests
Manual UI testing should cover:
- print-calendar-events button is visible
- send-email-notifications button is visible
- print calendar events date picker defaults to today (local date via getFullYear/getMonth/getDate)
- send email notifications date picker defaults to tomorrow
- selected date submission succeeds for both task types
- running task appears in task list with correct task type label
- stage labels update correctly (including "Mail Worker creating email drafts")
- email task shows progress: "N of M drafts created, K skipped"
- cancel button triggers cancellation
- cancelled task updates to cancelled state
- zero-event day shows COMPLETED
- completed task shows COMPLETED
- invalid date input is blocked

---

## External Dependency Rules

### Google Calendar
Automated tests must not:
- call live Google Calendar APIs
- require live OAuth browser consent
- require real `credentials.json` or `token.json`

Tests mock:
- the Google Calendar service object (inject a mock service into CalendarWorker)
- the `events().list()` response
- the OAuth flow (inject a pre-authorized credential or mock the auth flow)

### Google Gmail
- Gmail API must be mocked
- no live OAuth in tests
- no real email creation

### PDF Generation
- automated tests may generate real PDFs in a temporary directory
- use `tmp_path` pytest fixture to redirect DocumentWorker output
- do not require manual file inspection; assert file existence and basic metadata

### Printing
- automated tests must not send jobs to a real printer
- inject a fake print adapter that records print calls and returns success or failure on demand

---

## Component Specific Testing Rules

### OfficeAdmin
Must test:
- unique UUID4 request ID creation
- initial PENDING state with all expected fields present
- transition to RUNNING / GETTING_CALENDAR_EVENTS
- transition to CREATING_EVENT_PDFS with correct `documents_expected` count
- transition to PRINTING_EVENT_PDFS with correct `prints_expected` count
- terminal COMPLETED
- UNKNOWN handling for missing request ID
- cancellation state machine for each stage
- `cancel_requested` flag set correctly
- failure → ERROR with error details recorded
- late-arriving callbacks discarded when terminal
- queue full rejection
- shutdown without hanging

### OfficeAdmin Email Task Testing

Must test:
- submit_send_email_notifications returns UUID4 request ID
- initial status has task_type=SEND_EMAIL_NOTIFICATIONS, emails_expected=0, all counters=0
- stage: PENDING → GETTING_CALENDAR_EVENTS → CREATING_EMAIL_DRAFTS
- all events complete (email_draft_complete) → COMPLETED
- all events skipped (email_draft_skipped) → COMPLETED (skipped counts toward total)
- mix of complete, skipped, and failed(cancel) → CANCELLED once all arrive
- zero-event day → COMPLETED without calling MailWorker
- email_draft_failed (real error) → ERROR and cancel propagated
- cancellation during GETTING_CALENDAR_EVENTS → CANCELLED
- cancellation during CREATING_EMAIL_DRAFTS → CANCELLED when all email callbacks arrive
- late callback after terminal state → silently discarded

### CalendarWorker
Must test:
- request enqueue and return immediately
- successful callback with normalized events
- zero-event day callback
- failure callback on API error
- all-day event normalization
- timed event normalization
- cancellation callback (`calendar_events_failed` with `"Cancelled"`)
- OAuth failure → failure callback
- shutdown

### DocumentWorker
Must test:
- request enqueue and return immediately
- one PDF created per event
- output path reported in `document_complete` callback
- file naming is safe and deterministic
- multiple concurrent event jobs for one request
- cancellation callback (`document_failed` with `"Cancelled"`)
- real-error failure callback
- shutdown

### PrinterWorker
Must test:
- request enqueue and return immediately
- successful print completion callback
- failure callback on print error
- cancellation callback (`print_failed` with `"Cancelled"`)
- invalid document path → failure callback
- shutdown

### MailWorker

Must test:
- `_extract_recipients`: single email address in description text → correct address returned
- `_extract_recipients`: multiple addresses in description text → all unique addresses in order
- `_extract_recipients`: duplicate addresses → deduplicated
- `_extract_recipients`: no email addresses → empty list
- `_extract_recipients`: None description → empty list
- event with recipients → Gmail API called → `email_draft_complete` (mock service)
- event with no recipients → `email_draft_skipped`; Gmail API not called
- Gmail API failure → `email_draft_failed` with error text (mock service raises)
- cancellation before processing → `email_draft_failed(..., "Cancelled")`
- cancellation after recipient extraction → `email_draft_failed(..., "Cancelled")`
- OAuth failure (service_factory raises) → `email_draft_failed`
- shutdown → no hanging tasks
- every work item produces exactly one callback




---

## Minimum Test Requirements Per Feature

### New Worker
- one happy-path unit test
- one error-path unit test
- one cancellation test
- one orchestration/callback test
- one shutdown test

### New OfficeAdmin Task Type
- start-request test (correct task_type, counters=0, stage=PENDING)
- each stage-transition test
- completion test
- all-skipped completion test (if the task type has a "skipped" callback)
- cancellation test at each stage
- error test for each worker component in the pipeline

### New API Endpoint
- one success test
- one invalid-input test
- one dependency-failure test

### New UI Feature
- one API test where applicable
- manual browser verification steps documented

---

## Mocking and Dependency Injection Guidelines
Code must be structured so that:
- OfficeAdmin accepts worker instances as constructor arguments
- CalendarWorker accepts a Google service factory or mock service
- DocumentWorker accepts an output directory path (default `reports/`)
- PrinterWorker accepts a print adapter (default system printer; injectable fake for tests)
- All components accept `asyncio.AbstractEventLoop` or use `asyncio.get_event_loop()` (mockable)
- OAuth flow is isolated behind a callable that can be replaced in tests

---

## Status and Stage Assertions
Tests must assert:
- specific `stage` values, not just `RUNNING`
- specific `status` values
- progress counters (`documents_completed`, `prints_completed`, etc.)
- `cancel_requested` flag
- `errors` list contents when failures occur

---

## Validation Commands
Required:
```
pytest
pre-commit run --all-files
ruff check .
ruff format --check .
mypy .
```

---

## Manual Test Checklist for First Release
1. start the app
2. click `Print Calendar Events`
3. confirm calendar widget appears
4. confirm today is highlighted by default (verify date matches local system date, not UTC)
5. choose a date and submit
6. confirm request appears in running-tasks list
7. confirm stages update: GETTING_CALENDAR_EVENTS → CREATING_EVENT_PDFS → PRINTING_EVENT_PDFS → COMPLETED
8. cancel an in-progress task and verify UI updates to CANCELLED
9. submit again, let it complete, verify COMPLETED state
10. verify a zero-event day reaches COMPLETED immediately
11. verify an unknown request ID returns UNKNOWN
12. verify server restart clears all task state from the UI
