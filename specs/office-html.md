# Feature: Office Admin HTML Frontend

## Status
Final

## Version
v1.1

## Summary
Create an HTML frontend for the OfficeAdmin system.

The first supported UI workflow is printing calendar events for a selected date.

---

## Goals
1. Provide a simple browser UI for office automation
2. Support the initial print-calendar-events workflow
3. Make the selected date easy to choose with today as the default
4. Surface task progress and stage information clearly
5. Support cancellation from the UI
6. Remain extensible for future OfficeAdmin tasks

---

## Non-Goals
- frontend framework (plain HTML, CSS, JS only)
- authentication
- multi-user dashboards
- WebSocket or SSE in v1 (polling only)
- highly customized visual design

---

## Required UI Elements

### Print Calendar Events Control
- a button labeled `Print Calendar Events`
- when pressed: show a `<input type="date">` widget defaulted to today
- a `Submit` button to confirm the selected date
- on submit: POST to `/api/office/print-calendar-events`, store returned `request_id`, begin polling


### Send Email Notifications Control
- a button labeled `Send Email Notifications`
- when pressed: show a `<input type="date">` widget defaulted to tomorrow
- a `Submit` button to confirm the selected date
- on submit: POST to `/api/office/send-email-notifications`, store returned `request_id`, begin polling
- on 400: show "Invalid date" message
- on 429: show "Server busy, please try again"

### Running Tasks Area
A section listing all tracked tasks (active and recently completed).

For each task, display:
- request ID (abbreviated or full)
- task type (human-readable label)
- selected date
- top-level status
- current stage (human-readable label)
- progress details appropriate to task type:
  - PRINT_CALENDAR_EVENTS: e.g., "2 of 5 PDFs created" / "3 of 5 PDFs printed"
  - SEND_EMAIL_NOTIFICATIONS: e.g., "2 of 5 drafts created, 1 skipped"
- cancel button (shown only when task is not terminal)

---

## Stage Display Labels

| Stage value | UI label |
|---|---|
| `PENDING` | Pending |
| `GETTING_CALENDAR_EVENTS` | Calendar Worker getting calendar events |
| `CREATING_EVENT_PDFS` | Document Worker creating PDFs |
| `PRINTING_EVENT_PDFS` | Printer Worker printing PDFs |
| `CREATING_EMAIL_DRAFTS` | Mail Worker creating email drafts |
| `COMPLETED` | Completed |
| `CANCELLED` | Cancelled |
| `ERROR` | Error |

---

## Frontend Behavior

### Date Defaults
Use local-time getters to build `YYYY-MM-DD` strings. This avoids UTC drift (unlike `toISOString`) and locale-specific formatting differences (unlike `toLocaleDateString`).

**Today** (used by Print Calendar Events):
```javascript
const _d = new Date();
datePicker.value = `${_d.getFullYear()}-${String(_d.getMonth() + 1).padStart(2, "0")}-${String(_d.getDate()).padStart(2, "0")}`;
```

**Tomorrow** (used by Send Email Notifications):
```javascript
const _d = new Date();
_d.setDate(_d.getDate() + 1);
datePicker.value = `${_d.getFullYear()}-${String(_d.getMonth() + 1).padStart(2, "0")}-${String(_d.getDate()).padStart(2, "0")}`;
```

### Task Submission
1. user confirms date
2. fetch the appropriate endpoint (`/api/office/print-calendar-events` or `/api/office/send-email-notifications`)
3. on success: store `request_id` in task list; begin polling
4. on 400: show "Invalid date" message
5. on 429: show "Server busy, please try again"

### Status Polling
- poll `/api/office/status/{request_id}` every 2 seconds while task is not terminal
- on each response: update displayed stage, status, and progress counters
- stop polling when status is COMPLETED, CANCELLED, or ERROR

### Cancellation
- each non-terminal task row shows a `Cancel` button
- on click: `fetch("POST /api/office/cancel/{request_id}")`
- on response: update the task display immediately; polling continues until CANCELLED is confirmed

### Task Persistence
- tasks are tracked in a JavaScript array in-page memory
- task list is lost on page reload (consistent with server-side in-memory store)
- no localStorage or session storage needed in v1

---

## File Layout

```text
templates/
  index.html

static/
  app.js
  styles.css
```

FastAPI serves `templates/index.html` at `GET /` and mounts `/static`.

---

## Extensibility
The JS should be structured so future office task types can be added:
- a `TASK_ACTIONS` map: task type → function that renders the submission control
- a `STAGE_LABELS` map: stage value → human-readable string
- a `renderTask(task)` function used for all task types
- API helpers (`apiPost`, `apiGet`) shared across all task types

---

## Acceptance Criteria
1. page shows `Print Calendar Events` button
2. page shows `Send Email Notifications` button
3. clicking Print Calendar Events shows a date picker defaulted to today
4. clicking Send Email Notifications shows a date picker defaulted to tomorrow
5. submitting either form calls the correct backend endpoint and adds the task to the list
6. task list shows stage labels that update as work progresses
7. CREATING_EMAIL_DRAFTS stage shows "Mail Worker creating email drafts"
8. cancel button appears for active tasks
9. cancel action updates the task display
10. zero-event day: task reaches COMPLETED
11. UI structure allows future office tasks without a rewrite

---

## Test Plan (Manual)
1. page loads with button visible
2. clicking button shows date picker with today selected
3. changing and submitting a date creates a new task row
4. task stage labels update in real time
5. cancel button is visible for active tasks
6. pressing cancel transitions task toward CANCELLED
7. completed task shows COMPLETED and no cancel button
8. server restart clears task list on next page load
9. 429 error shows user-friendly "Server busy" message
10. invalid date blocked by browser date input (type="date")
