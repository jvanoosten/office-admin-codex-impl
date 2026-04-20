# Office Admin Implementation Roadmap

## Purpose
This roadmap defines a phased delivery plan based on the current architecture and feature specifications in `docs/` and `specs/`.

The plan intentionally stages implementation to support incremental integration and testing without starting every component at once.

---

## Phase 1 — Core Print Flow (Frontend + Backend + Office Admin + Calendar Worker)

### Scope
Implement the minimum end-to-end slice for the **Print Calendar Events** task with observability-focused outputs.

### Deliverables
1. **HTML/JavaScript frontend (initial task UI)**
   - Add Print Calendar Events control (button + date picker + submit).
   - Show task list entries with request ID, status, and stage.
   - Poll task status endpoint and refresh row state.
   - Include temporary testing output: render calendar event details returned for the request.

2. **FastAPI backend**
   - Wire lifespan startup/shutdown for initialized components in this phase.
   - Add:
     - `POST /api/office/print-calendar-events`
     - `GET /api/office/status/{request_id}`
     - `POST /api/office/cancel/{request_id}`
   - Validate request date and return API errors per current specs.

3. **OfficeAdmin orchestration (partial pipeline)**
   - Support `PRINT_CALENDAR_EVENTS` submission and in-memory status tracking.
   - Dispatch calendar lookup work to CalendarWorker.
   - Track stage transitions through calendar retrieval.
   - Return enough detail for frontend testing visibility.

4. **CalendarWorker**
   - Implement async queue worker, cancellation checks, and callbacks.
   - Integrate Google Calendar retrieval with local-time query boundaries.
   - Normalize and return filtered events.

### Temporary testing behavior required in this phase
- Show the details of each calendar event on the web interface to confirm retrieval and normalization behavior before document/print integration is complete.

### Exit criteria
- User can submit a date from the frontend and see a request progress through calendar retrieval.
- Calendar events for that request are visible in the UI.
- Cancellation and status polling work for in-progress requests.

---

## Phase 2 — Document Generation Integration

### Scope
Add the DocumentWorker stage into the existing print pipeline and shift frontend testing outputs accordingly.

### Deliverables
1. **DocumentWorker**
   - Implement async queue, cancellation behavior, per-event PDF generation, and callbacks.
   - Write outputs to `reports/`.
   - Apply stale PDF pruning rules.

2. **OfficeAdmin pipeline extension**
   - On calendar completion, dispatch per-event document requests.
   - Track document counters and document paths.
   - Advance stage to `CREATING_EVENT_PDFS`.

3. **Frontend testing output update**
   - Show names/paths of generated documents in the task view.
   - **Stop printing calendar entry details on the web interface**.

### Exit criteria
- Calendar events trigger document creation jobs.
- Generated document names/paths are visible for testing.
- UI no longer displays raw calendar event details.

---

## Phase 3 — Printer Integration Completion

### Scope
Complete the Print Calendar Events workflow by integrating PrinterWorker.

### Deliverables
1. **PrinterWorker**
   - Implement async queue, cancellation checks, file existence validation, executor-based print submission, and callbacks.

2. **OfficeAdmin final print-stage orchestration**
   - Dispatch print jobs after document completion.
   - Track print counters and terminal completion/cancellation/error transitions.
   - Enforce failure handling policy (first real failure transitions task to `ERROR`).

3. **Frontend progress visibility**
   - Show print-stage progress (`prints_completed` vs `prints_expected`).
   - Preserve cancel behavior for non-terminal tasks.

### Exit criteria
- End-to-end print workflow is complete from submit through print completion.
- Stage progression: `GETTING_CALENDAR_EVENTS` → `CREATING_EVENT_PDFS` → `PRINTING_EVENT_PDFS` → terminal state.

---

## Phase 4 — Email Notifications Workflow + Frontend Expansion

### Scope
Introduce the Send Email Notification task and complete all initial architecture/spec functionality.

### Deliverables
1. **MailWorker**
   - Implement async queue, cancellation checks, recipient extraction from description text, Gmail draft creation, skipped-event behavior, and callbacks.

2. **OfficeAdmin second task type**
   - Add `SEND_EMAIL_NOTIFICATIONS` submission method and status counters.
   - Route calendar results into mail-draft creation stage.
   - Handle complete/skipped/failed callback outcomes and terminal transitions.

3. **Backend API expansion**
   - Add `POST /api/office/send-email-notifications`.
   - Ensure status payload supports both task types.

4. **Frontend task expansion**
   - Add Send Email Notification task control.
   - Default date picker to tomorrow for email task.
   - Render email task progress and stage label `CREATING_EMAIL_DRAFTS`.

### Exit criteria
- Both initial task types are supported from UI through backend orchestration and worker execution.
- Architecture and specification scope for the initial release is fully implemented.

---

## Cross-Document Issues Identified During Review (to resolve before/during implementation)

1. **Recipient extraction rule conflict**
   - Some docs describe recipient extraction as `mailto:`-only, while MailWorker spec defines extraction of any email addresses found in event description text.
   - Resolution: standardize on the MailWorker behavior (regex-based extraction of email addresses anywhere in description).

2. **FastAPI invalid-date response inconsistency**
   - Backend spec states invalid date uses 422 (Pydantic), but some test-plan lines reference 400.
   - Resolution: align docs/tests to 422 unless explicit custom validation is introduced.

3. **FastAPI lifespan naming inconsistency**
   - Example snippet uses mixed `office_worker`/`office_admin` naming in `app.state`.
   - Resolution: standardize on `office_admin` naming in snippets and route examples.
