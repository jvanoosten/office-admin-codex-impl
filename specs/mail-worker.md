# Feature: MailWorker

## Status
Final

## Version
v1.1

## Summary
Implement a `MailWorker` that creates Gmail draft email notifications for calendar events. For each event, MailWorker extracts all unique email addresses found anywhere in the event description and creates a Gmail draft addressed to those recipients. Events with no email addresses in their description are skipped without error.

---

## Problem
The `SEND_EMAIL_NOTIFICATIONS` workflow requires composing email notification drafts for calendar events without coupling Gmail logic into OfficeAdmin or FastAPI.

---

## Goals
1. Provide a `MailWorker` class using asyncio
2. Accept asynchronous work requests via asyncio.Queue
3. Extract all unique email addresses found anywhere in `event.description`
4. Create one Gmail draft per event that has at least one recipient
5. Skip events with no email addresses in the description (not an error)
6. Report completion, skipped, and failure results to OfficeAdmin via callbacks
7. Handle Google OAuth for Gmail inline on first use
8. Support cancellation
9. Compose email content from a plain-text template file

---

## Non-Goals
- sending email (drafts only, no auto-send)
- fetching calendar events
- orchestration logic
- per-event recipient editing in the UI (v1 is fully automated)

---

## Constructor

```python
def __init__(
    self,
    service_factory=None,
    credentials_path: str = "gmail_credentials.json",
    token_path: str = "gmail_token.json",
) -> None
```

Initializes:
- `asyncio.Queue()` for work items
- background `asyncio.Task` running the worker loop
- `dict[str, bool]` for cancellation state per request ID
- stored credential/token paths

`service_factory`: optional callable `() -> resource`. If provided, it is called to build the Gmail API resource instead of the default auth/build flow. Used in tests to inject a mock service.

Does not perform OAuth at construction time. OAuth is deferred to the first work item.

---

## Public Methods

### `create_email_draft(office_admin_ref, request_id, event) -> None`

Enqueues a draft-creation request for a single calendar event.

Behavior:
1. create a work item: `{office_admin_ref, request_id, event}`
2. `queue.put_nowait(item)`
3. return immediately

---

### `cancel_request(request_id: str) -> None`

Marks the request as cancelled.

Behavior:
- set `cancelled[request_id] = True`
- the worker loop checks this flag before processing and before calling any callback

---

### `async shutdown() -> None`

Behavior:
1. enqueue `None` sentinel
2. `await` the worker task

---

## Worker Loop

```python
async def _worker_loop(self):
    while True:
        item = await self._queue.get()
        if item is None:
            break
        await self._process_item(item)
```

### `_process_item` behavior:

1. check `cancelled[request_id]`; if True: call `await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")`; return
2. extract `event_id = event["id"]`
3. extract recipients using `_extract_recipients(event.get("description", ""))`
4. if no recipients:
   - check `cancelled[request_id]` again; if True: call `email_draft_failed(..., "Cancelled")`; return
   - call `await office_admin_ref.email_draft_skipped(request_id, event_id)`; return
5. build Gmail service (run in executor if not already built; see OAuth Handling)
6. compose draft message using the email template (see Draft Content)
7. create draft via Gmail API (in executor): `service.users().drafts().create(userId="me", body={"message": encoded_message}).execute()`
8. extract `draft_id` from response
9. check `cancelled[request_id]` again; if True: call `email_draft_failed(..., "Cancelled")`; return (draft may already be created; cancellation is best-effort at this point)
10. call `await office_admin_ref.email_draft_complete(request_id, event_id, draft_id)`

On any exception during steps 5–8:
- call `await office_admin_ref.email_draft_failed(request_id, event_id, str(exception))`

---

## Recipient Extraction

Implemented in `_extract_recipients(description)`:

```python
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")

@staticmethod
def _extract_recipients(description: str) -> list[str]:
    matches = _EMAIL_RE.findall(description or "")
    seen = set()
    result = []
    for addr in matches:
        if addr not in seen:
            seen.add(addr)
            result.append(addr)
    return result
```

Rules:
- scans the entire description text for any email address pattern
- case-preserving; duplicates removed, first occurrence kept
- if `description` is `None` or empty, returns `[]`

---

## Google OAuth Handling

OAuth is handled inline on the first work item that requires the Gmail service:

1. check for a valid token at `token_path` (`gmail_token.json`)
2. if no valid token: run the Gmail OAuth flow in an executor (blocking), using credentials from `credentials_path` (`gmail_credentials.json`)
3. required scope: `https://www.googleapis.com/auth/gmail.compose`
4. if OAuth fails: call `email_draft_failed` with the auth error; return
5. store the obtained token at `token_path` for reuse
6. on subsequent requests: load the token from `token_path`; refresh if expired (handled transparently by the Google auth library)

The Gmail credentials file (`gmail_credentials.json`) and calendar OAuth token (`token.json`) are separate files so users can independently authorize each set of Google permissions.

For testability, `service_factory` is injectable. When provided, it bypasses OAuth entirely.

---

## Draft Content

Email drafts are composed from a plain-text template file:

```
templates/email_notification_template
```

### Template format

- **Line 1** — the email subject (stripped of surrounding whitespace)
- **Remaining lines** — the email body

The body may contain the following placeholders, which are replaced at compose time:

| Placeholder | Replaced with |
|-------------|---------------|
| `{date}` | Full local date of the event start (e.g., `Saturday, April 12, 2026`) |
| `{time}` | Local start – end time range (e.g., `9:00 AM – 10:00 AM`); `(all day)` for all-day events |
| `{location}` | Value of `event["location"]`; empty string if `None` |

### Template loading

- Loaded from disk by `_load_template()` on each compose call (path resolved relative to the `src/` package directory: `../templates/email_notification_template`)
- An optional `template` parameter on `_compose_draft_body` accepts a pre-loaded string; used in tests to avoid filesystem access

### Default template (initial)

```
Example Company Upcoming Service

Dear Customer,

Example Company has upcoming services planned for you.

Date: {date}
Time: {time}

Location: {location}

We look forward to exceeding your expectations.

Thank you!

Example Company
Somewhere, USA

examplecompany.com
Phone: (555) 555-5555
```

### Draft encoding

- MIME type: `text/plain; charset=utf-8`
- Encoded as base64 URL-safe per Gmail API `raw` message format

---

## Callbacks

All callbacks are `async` methods on `office_admin_ref`.

### `email_draft_complete(request_id, event_id, draft_id)`
Called when a Gmail draft was created successfully.
- `draft_id`: the Gmail API draft ID string returned by `drafts().create()`

### `email_draft_skipped(request_id, event_id)`
Called when an event description contains no email addresses. This is not an error; it is a normal outcome for events without notification recipients.

### `email_draft_failed(request_id, event_id, error_text)`
Called when draft creation fails or the item is cancelled.
- `error_text = "Cancelled"` for cancellation
- `error_text = str(exception)` for real failures

---

## Acceptance Criteria
1. work requests are enqueued and return immediately
2. `_extract_recipients` returns all unique email addresses found anywhere in the description
3. `_extract_recipients` returns `[]` for empty or `None` description
4. `_extract_recipients` removes duplicate addresses; first occurrence is kept
5. event with no email addresses in the description → `email_draft_skipped` callback (not `email_draft_failed`)
6. event with one or more recipients → Gmail draft created → `email_draft_complete` callback with `draft_id`
7. Gmail API failure → `email_draft_failed` callback with error text
8. cancellation before processing → `email_draft_failed(..., "Cancelled")`
9. cancellation after recipient extraction but before Gmail API call → `email_draft_failed(..., "Cancelled")`
10. OAuth runs inline on first work item; subsequent requests reuse the token
11. OAuth failure → `email_draft_failed` with auth error
12. shutdown completes without hanging
13. every work item produces exactly one callback: `complete`, `skipped`, or `failed`
14. email subject is taken from the first line of `templates/email_notification_template`
15. `{date}`, `{time}`, and `{location}` placeholders in the template body are replaced with event values
16. template can be overridden via `_compose_draft_body(event, recipients, template=...)` for test isolation

---

## Test Plan
1. `_extract_recipients`: single email anywhere in description → returns it
2. `_extract_recipients`: multiple emails → returns all in order
3. `_extract_recipients`: duplicate addresses → deduplicated; first occurrence retained
4. `_extract_recipients`: no email address → returns `[]`
5. `_extract_recipients`: `None` description → returns `[]`
6. `_extract_recipients`: email embedded mid-sentence → extracted correctly
7. `_extract_recipients`: emails on different lines → all extracted
8. event with recipients → Gmail API called → `email_draft_complete` callback (mock service)
9. event with no email addresses → `email_draft_skipped` callback; Gmail API not called
10. Gmail API failure → `email_draft_failed` callback (mock service raises)
11. cancellation before processing → `email_draft_failed(..., "Cancelled")`
12. cancellation after recipients extracted but before draft creation → `email_draft_failed(..., "Cancelled")`
13. OAuth failure (service_factory raises) → `email_draft_failed` with auth error
14. shutdown after all items processed → no hanging
15. draft subject is the first line of the template (injected string in test)
16. `{date}`, `{time}`, `{location}` placeholders are replaced; no literal `{...}` remains in body
17. `{location}` is replaced with the event location value
18. default (no template arg): subject is `"Example Company: Upcoming Service"`; body contains `"Example Company"`
