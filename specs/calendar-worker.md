# Feature: Calendar Worker

## Status
Final

## Version
v1.

## Summary
Implement a `CalendarWorker` that retrieves Google Calendar events for a specified day and reports results back to OfficeAdmin via callbacks.

---

## Problem
The office automation workflow needs a specialized component to retrieve calendar events without putting Google Calendar logic inside OfficeAdmin or FastAPI.

---

## Goals
1. Provide a `CalendarWorker` class using asyncio
2. Accept asynchronous work requests via asyncio.Queue
3. Retrieve timed events for a specified day that fall within the 08:00–18:00 local window
4. Normalize event data
5. Use Google Calendar APIs
6. Handle Google OAuth inline on first use
7. Support cancellation

---

## Non-Goals
- editing or deleting calendar events
- UI behavior
- PDF generation or printing
- multi-calendar support (v1 uses primary calendar only)

---

## Constructor

```python
def __init__(
    self,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> None
```

Initializes:
- `asyncio.Queue()` for work items
- background `asyncio.Task` running the worker loop
- `dict[str, bool]` for cancellation state per request ID
- stored credential/token paths

Does not perform OAuth at construction time. OAuth is deferred to the first work item.

---

## Public Methods

### `get_events_for_date(office_admin_ref, request_id, selected_date) -> None`

Enqueues a request to retrieve all calendar events for the specified date.

Behavior:
1. create a work item: `{office_admin_ref, request_id, selected_date}`
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
1. check `cancelled[request_id]`; if True: call `calendar_events_failed(request_id, selected_date, "Cancelled")`; return
2. build Google Calendar service (run in executor if not already built)
3. retrieve events for the selected date (in executor)
4. filter events (see Event Filtering below)
5. normalize remaining events
6. check `cancelled[request_id]` again; if True: call `calendar_events_failed(request_id, selected_date, "Cancelled")`; return
7. call `await office_admin_ref.calendar_events_complete(request_id, selected_date, events)`

On any exception:
- call `await office_admin_ref.calendar_events_failed(request_id, selected_date, str(exception))`

---

## Google OAuth Handling

OAuth is handled inline on the first work item:

1. check for a valid token at `token_path`
2. if no valid token: run the OAuth flow in an executor (blocking):
   ```python
   await loop.run_in_executor(None, self._run_oauth_flow)
   ```
3. if OAuth fails: call `calendar_events_failed` with the auth error; return
4. store the obtained token at `token_path` for reuse
5. on subsequent requests: load the token from `token_path`; refresh if expired (handled transparently by the Google auth library)

For testability, the Google service construction must be injectable:
- constructor accepts optional `service_factory` callable; if provided, it is used instead of building a real service
- default: `build("calendar", "v3", credentials=...)`

---

## Google Calendar Query

To retrieve events for a selected date, the query window must use the **local system timezone**, not UTC. Using UTC produces incorrect results for users in timezones behind UTC (e.g. late-evening events on the previous local day are incorrectly included).

```python
import datetime

date = datetime.date.fromisoformat(selected_date)
next_day = date + datetime.timedelta(days=1)
local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
time_min = datetime.datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=local_tz).isoformat()
time_max = datetime.datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=local_tz).isoformat()
service.events().list(
    calendarId="primary",
    timeMin=time_min,
    timeMax=time_max,
    singleEvents=True,
    orderBy="startTime",
).execute()
```

`timeMax` is midnight at the start of the next day (exclusive), which is more correct than `23:59:59` and avoids missing events that start in the final second of the day.

The raw Google response may include all-day events and events outside the 08:00–18:00 window. These are removed by the event filter after fetching.

---

## Event Filtering

After fetching raw events and before normalizing, apply `_is_printable_event(raw)` to each raw event. Only events that pass all three rules are forwarded to OfficeAdmin:

1. **Not an all-day event** — raw event must have `start.dateTime` (timed). Events with only `start.date` are excluded.
2. **Starts at or after 08:00 local time** — parse `start.dateTime` as a timezone-aware datetime, convert to local system time, check `start_local.time() >= time(8, 0)`.
3. **Ends at or before 18:00 local time** — parse `end.dateTime` as a timezone-aware datetime, convert to local system time, check `end_local.time() <= time(18, 0)`.

Both boundary values are inclusive (08:00 and 18:00 are valid).

If parsing fails for any event, that event is excluded and a warning is logged.

```python
@staticmethod
def _is_printable_event(raw: dict) -> bool:
    start_str = raw.get("start", {}).get("dateTime")
    end_str = raw.get("end", {}).get("dateTime")
    if not start_str or not end_str:
        return False  # all-day event
    try:
        start_local = datetime.datetime.fromisoformat(
            start_str.replace("Z", "+00:00")
        ).astimezone()
        end_local = datetime.datetime.fromisoformat(
            end_str.replace("Z", "+00:00")
        ).astimezone()
        return (
            start_local.time() >= datetime.time(8, 0)
            and end_local.time() <= datetime.time(18, 0)
        )
    except (ValueError, AttributeError):
        return False
```

---

## Normalized Event Structure

```python
{
    "id": str,            # required
    "summary": str,       # required; default to "" if absent
    "start": str,         # ISO 8601; dateTime or date field from Google
    "end": str,           # ISO 8601; dateTime or date field from Google
    "timezone": str | None,
    "location": str | None,
    "description": str | None,
    "html_link": str | None,
    "status": str | None,
    "colorId": str | None,  # Google Calendar color ID (e.g. "7"); used by DocumentWorker for header color
}
```

Normalization rules:
- for timed events: use `event["start"]["dateTime"]`
- for all-day events: use `event["start"]["date"]`
- same for `end`
- `timezone`: use `event["start"]["timeZone"]`
- missing optional fields normalize to `None`

---

## Acceptance Criteria
1. work requests are enqueued and return immediately
2. CalendarWorker retrieves events for the specified day
3. returned event structure is normalized
4. all-day events are excluded from results; only timed events are returned
5. `calendar_events_complete` is called on success
6. `calendar_events_failed` is called on failure
7. `calendar_events_failed("Cancelled")` is called when cancelled
8. OAuth runs inline on first request; subsequent requests reuse the token
9. OAuth failure → `calendar_events_failed` with auth error
10. shutdown completes without hanging
11. Google Calendar query uses local timezone boundaries, not UTC — `timeMin` is local midnight on the selected date; `timeMax` is local midnight on the following date
12. normalized events include `colorId` from the raw Google response (or `None` if absent)
13. timed events starting before 08:00 local are excluded
14. timed events ending after 18:00 local are excluded
15. timed events with start ≥ 08:00 and end ≤ 18:00 are included; both boundaries are inclusive
16. all-day events are always excluded regardless of the time window

---

## Test Plan
1. request enqueue returns immediately
2. successful callback with normalized events (mock service)
3. zero-event day → empty list callback
4. failure callback on API error (mock service raises exception)
5. all-day event excluded from results
6. timed event normalization
7. cancellation before processing → `calendar_events_failed("Cancelled")`
8. cancellation after retrieval but before callback → `calendar_events_failed("Cancelled")`
9. OAuth failure → `calendar_events_failed` with auth error (mock service factory raises)
10. shutdown
11. `_fetch_events` passes local-timezone `timeMin`/`timeMax` to the Google API (not UTC `Z` suffix)
12. normalized event includes `colorId` when present in the raw Google event; `None` when absent
13. `_is_printable_event`: all-day event (no dateTime) → False
14. `_is_printable_event`: start before 08:00 → False
15. `_is_printable_event`: end after 18:00 → False
16. `_is_printable_event`: start exactly 08:00, end exactly 18:00 → True (boundaries inclusive)
17. `_is_printable_event`: start 09:00, end 17:00 → True
18. mixed day (all-day + in-window + out-of-window events) → only in-window timed events returned
