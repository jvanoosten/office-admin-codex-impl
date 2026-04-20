# Feature: Document Worker

## Status
Final

## Version
v1.6

## Summary
Implement a `DocumentWorker` that creates one PDF document per calendar event and reports completion back to OfficeAdmin via callbacks.

---

## Problem
Document-generation logic must not live in OfficeAdmin or FastAPI. DocumentWorker isolates PDF creation and provides a clean async interface.

---

## Goals
1. Provide a `DocumentWorker` class using asyncio
2. Accept asynchronous work requests via asyncio.Queue
3. Create one PDF per event (in executor)
4. Write PDFs to the `reports/` directory (configurable for testing)
5. Report completion and failure back to OfficeAdmin
6. Support cancellation

---

## Non-Goals
- printer integration
- calendar retrieval
- orchestration of the overall office task
- multi-document bundling (v1)

---

## Constructor

```python
def __init__(
    self,
    output_dir: str = "reports",
) -> None
```

Initializes:
- `asyncio.Queue()` for work items
- background `asyncio.Task` running the worker loop
- `dict[str, bool]` for cancellation state per request ID
- `output_dir` for PDF output (injectable for tests via `tmp_path`)

---

## Public Methods

### `create_event_document(office_admin_ref, request_id, event) -> None`

Enqueues a document-generation request for a single event.

Behavior:
1. validate that `event` has at minimum an `id` field
2. create work item: `{office_admin_ref, request_id, event}`
3. `queue.put_nowait(item)`
4. return immediately

---

### `cancel_request(request_id: str) -> None`

Marks the request as cancelled.

Behavior:
- set `cancelled[request_id] = True`

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
1. check `cancelled[request_id]`; if True: call `await office_admin_ref.document_failed(request_id, event_id, "Cancelled")`; return
2. generate output file path using the naming rules below
3. run PDF generation in executor:
   ```python
   await loop.run_in_executor(None, self._generate_pdf, event, output_path)
   ```
4. check `cancelled[request_id]` again; if True: call `await office_admin_ref.document_failed(request_id, event_id, "Cancelled")`; return
5. call `await office_admin_ref.document_complete(request_id, event_id, output_path)`

On any exception during PDF generation:
- call `await office_admin_ref.document_failed(request_id, event_id, str(exception))`

---

## File Naming Rules

File names are deterministic, filesystem-safe, and collision-aware.

Components:
- first 8 characters of `request_id` (UUID prefix)
- `event["id"]` sanitized: alphanumeric and hyphens only, max 20 chars
- sanitized `event["summary"]` fragment: alphanumeric and hyphens only, lowercase, max 40 chars
- `.pdf` extension

Assembly:
```
{request_id[:8]}_{safe_event_id}_{safe_summary}.pdf
```

Example:
```
3f2504e0_abc123def456_team-standup.pdf
```

Sanitization function: replace all non-alphanumeric characters with `-`, collapse consecutive `-`, strip leading/trailing `-`.

If `summary` is absent or empty, use `event` as the summary fragment.

---

## Reports Directory Pruning

Before generating each new PDF, DocumentWorker prunes stale files from `output_dir`.

Rules:
- Only `.pdf` files (case-insensitive) in `output_dir` are considered; subdirectories are ignored.
- A file is stale if its modification time (`mtime`) is more than **7 days** before the current wall-clock time.
- Pruning runs in the executor (blocking filesystem I/O) immediately before PDF generation begins, after the pre-work cancellation check.
- Each file deletion is attempted independently; a failure to delete one file is logged at `WARNING` level and does not abort pruning or document generation.
- The age threshold is injectable via constructor parameter `prune_age_days: int = 7` to support testing without waiting real time.

---

## PDF Layout

### Constants (defined at module level)

```python
CUSTOMER_EMAIL = "examplecompany@gmail.com"
CUSTOMER_TITLE = "Created by: Example Company"
COLUMN_WIDTH = 4.25  # inches
```

---

### Page Structure (top to bottom)

```
┌══╗──────────────────────────────────────────────┐
│  │                        CUSTOMER_EMAIL (black) │
╚══╝──────────────────────────────────────────────┘
│  EVENT TITLE  (24pt, wrapped)                   │
│  CUSTOMER_TITLE  (small font)                   │
├─────────────────────────────────────────────────┤
│                                                 │
│  Time                                           │
│  9am – 5pm (Central Time - Chicago)  (18pt)     │
│                                                 │
│  Date                                           │
│  Wed Apr 8, 2026  (18pt)                        │
│                                                 │
│  Where                                          │
│  123 Main St  (18pt)                            │
│                                                 │
│  Description                                    │
│  Lorem ipsum…  (12pt)                           │
│                                                 │
└─────────────────────────────────────────────────┘
```
(`══` = 1-inch color block; `──` = no fill)

---

### Header Bar

- A filled rectangle drawn directly on the canvas, positioned at the **left edge** of the page, **1 inch wide** and **0.5 inches tall**. The remainder of the header row has no fill.
- Fill color: determined by `event["colorId"]` using the Google Calendar color mapping below. If `colorId` is absent or not in the mapping, fall back to light blue — RGB `(0.53, 0.81, 0.98)`.
- `CUSTOMER_EMAIL` string drawn **right-justified** across the full page width (`canvas.drawRightString` at `PAGE_W - rightMargin`), unchanged.
- Font: Helvetica, 10pt, **black** — RGB `(0, 0, 0)`.
- Text is vertically centered within the header row: `y = PAGE_H - HEADER_H + (HEADER_H - font_size) / 2`.
- The header elements are drawn using low-level canvas operations (`canvas.rect`, `canvas.drawRightString`), not as Platypus flowables.

#### Google Calendar `colorId` Color Mapping

| `colorId` | Name | Hex | ReportLab RGB |
|---|---|---|---|
| `"1"` | Lavender | `#7986CB` | `(0.475, 0.525, 0.796)` |
| `"2"` | Mint | `#27F0BA` | `(0.153, 0.941, 0.729)` |
| `"3"` | Grape | `#8E24AA` | `(0.557, 0.141, 0.667)` |
| `"4"` | Flamingo | `#E67C73` | `(0.902, 0.486, 0.451)` |
| `"5"` | Banana | `#F6BF26` | `(0.965, 0.749, 0.149)` |
| `"6"` | Tangerine | `#F4511E` | `(0.957, 0.318, 0.118)` |
| `"7"` | Peacock | `#039BE5` | `(0.012, 0.608, 0.898)` |
| `"8"` | Graphite | `#616161` | `(0.380, 0.380, 0.380)` |
| `"9"` | Blueberry | `#3F51B5` | `(0.247, 0.318, 0.710)` |
| `"10"` | Basil | `#0B8043` | `(0.043, 0.502, 0.263)` |
| `"11"` | Tomato | `#D50000` | `(0.835, 0.000, 0.000)` |
| absent / unknown | (default) | `#87CEFB` | `(0.530, 0.810, 0.980)` |

`colorId` values are strings in the Google Calendar API response. Match them as strings.

---

### Title

- Font: Helvetica-Bold, **fixed at 24pt**.
- Text wraps within the usable page width (left margin to right margin). Each wrapped line is drawn on its own baseline with 2pt inter-line spacing.
- If the title is absent or empty, use `"(No Title)"`.
- Placed immediately below the header bar with a small top padding (0.1 inches).

---

### Customer Title Line

- Text: `CUSTOMER_TITLE` constant (`"Created by: Example Company"`).
- Font: Helvetica, 9pt.
- Color: gray — RGB `(0.45, 0.45, 0.45)`.
- Placed immediately below the event title with a small gap (0.05 inches).

---

### Detail Column

All detail items are left-aligned and placed in a column of width `COLUMN_WIDTH` (4.5 inches), starting at the left margin.

A vertical gap of **0.5 inches** of white space follows each complete item (label + value block) before the next label.

#### Label style
- Font: Helvetica, 8pt.
- Color: gray — RGB `(0.45, 0.45, 0.45)`.
- Text is all-caps (e.g., `"TIME"`, `"DATE"`, `"WHERE"`, `"DESCRIPTION"`).

#### Time

Label: `"TIME"`

Value — time range with timezone on the same line:
- Format: `"9am – 5pm (Central Time - Chicago)"` (use en-dash `–`; use regular hyphen `-` inside the timezone display name).
  - Append the formatted timezone display name in parentheses on the same line, separated by a single space, if `event["timezone"]` is present.
  - All-day events: display `"All Day"` with no timezone suffix.
  - Timed events with no timezone field: display the time range only (e.g., `"9am – 5pm"`).
  - Timed events: format `start` and `end` using 12-hour clock, lowercase am/pm, drop `:00` for whole hours (e.g., `9am`, `9:30am`).
- Font: Helvetica-Bold, **fixed at 18pt**.
- The entire combined string is rendered as a single line in the same font and size.

**Timezone display name formatting:**

Convert the raw IANA timezone string (e.g. `"America/Chicago"`) to a human-readable display name using the following rules:

1. Extract the city: take the portion after the last `/`, replace underscores with spaces (e.g. `"America/New_York"` → `"New York"`).
2. Look up the standard name using a predefined mapping of IANA zone → standard name. If the zone is not in the mapping, use the region portion before the `/` as the fallback (e.g. `"America"` → `"America"`).
3. Combine as `"{Standard Name} - {City}"`.

Predefined mapping (minimum required entries):

| IANA timezone | Standard name |
|---|---|
| `America/New_York` | `Eastern Time` |
| `America/Detroit` | `Eastern Time` |
| `America/Chicago` | `Central Time` |
| `America/Denver` | `Mountain Time` |
| `America/Phoenix` | `Mountain Time` |
| `America/Los_Angeles` | `Pacific Time` |
| `America/Anchorage` | `Alaska Time` |
| `America/Adak` | `Hawaii-Aleutian Time` |
| `Pacific/Honolulu` | `Hawaii Time` |
| `Europe/London` | `Greenwich Mean Time` |
| `Europe/Paris` | `Central European Time` |
| `Europe/Berlin` | `Central European Time` |
| `Asia/Tokyo` | `Japan Time` |
| `Asia/Shanghai` | `China Time` |
| `Asia/Kolkata` | `India Time` |
| `Australia/Sydney` | `Australian Eastern Time` |
| `UTC` | `UTC` |

For any IANA timezone not in the mapping, fall back to the city name alone (e.g. `"America/Bogota"` → `"Bogota"`).

#### Date

Label: `"DATE"`

Value:
- Format: `"Wed Apr 8, 2026"` — abbreviated weekday, abbreviated month, day without leading zero, 4-digit year.
- Font: Helvetica-Bold, **fixed at 18pt**.

#### Where

Label: `"WHERE"`

Value:
- Source: `event["location"]` (omit the entire Where block if absent or empty).
- Font: Helvetica-Bold, **fixed at 18pt**.
- Text wraps within `COLUMN_WIDTH` if it exceeds one line.

#### Description

Label: `"DESCRIPTION"`

Value:
- Source: `event["description"]` (omit the entire Description block if absent or empty).
- **Pre-processing** — before rendering, clean the raw description text:
  1. Replace every `<br>` or `<br/>` or `<br />` tag (case-insensitive) with a newline character `\n`.
  2. Strip all remaining HTML/markdown tags using a regex that removes `<...>` sequences.
  3. Collapse runs of blank lines (more than one consecutive `\n`) down to a single blank line.
  4. Strip leading and trailing whitespace from the result.
- Font: Helvetica, **fixed at 12pt**.
- The description text wraps across multiple lines within `COLUMN_WIDTH`. Each `\n` in the pre-processed text forces a new line.
- If the wrapped text exceeds the remaining vertical space above the bottom margin, truncate to the number of lines that fit and append an ellipsis (`…`) to the last line.

---

### Margins

Canvas-based layout with explicit margins:
- `topMargin`: 0.35 inches below the header bar bottom edge (start of content area).
- `bottomMargin`: 0.5 inches.
- `leftMargin`: 0.5 inches.
- `rightMargin`: 0.5 inches.
- `pagesize`: `letter`.

The header bar is drawn using low-level canvas operations and is not affected by content margins.

---

## PDF Content Fields

| Field | Source | Required |
|---|---|---|
| Title | `event["summary"]` | No — default `"(No Title)"` |
| Header color | `event["colorId"]` | No — fall back to default light blue |
| Time range | `event["start"]`, `event["end"]` | Yes |
| Timezone | `event["start"]["timeZone"]` | No — fall back to local |
| Date | `event["start"]` | Yes |
| Location | `event["location"]` | No — omit block if absent |
| Description | `event["description"]` | No — omit block if absent |

Missing optional fields are omitted from the PDF layout without error.

---

## PDF Generation

PDF generation runs in an executor (it is a blocking operation).

Library: `reportlab`.

The `_generate_pdf(event, output_path)` method must be a plain synchronous function (no asyncio) suitable for `run_in_executor`.

For testability, the PDF generator function must be injectable:
- constructor accepts optional `pdf_generator` callable
- signature: `(event: dict, output_path: str) -> None`
- default: the real PDF generation implementation
- test fake: a callable that creates a minimal valid file (or empty file) at `output_path`

---

## Acceptance Criteria
1. work requests are enqueued and return immediately
2. one PDF is created per event in `output_dir`
3. file names follow the naming rules
4. `document_complete` callback includes the absolute output path
5. `document_failed` callback is sent on any exception
6. `document_failed("Cancelled")` is sent when cancelled (before or after generation)
7. cancellation does not leave partially-written files in inconsistent state
8. shutdown completes without hanging
9. generated PDF contains a 1-inch-wide color block at the left edge of the header row (0.5 inches tall) whose fill color matches `event["colorId"]` per the color mapping; falls back to default light blue when `colorId` is absent or unknown; the remainder of the header row has no fill; `CUSTOMER_EMAIL` is right-justified in black text across the full page width
10. event title is rendered at fixed 24pt and wraps to multiple lines when needed
11. `CUSTOMER_TITLE` appears below the title in small gray font
12. time, date, and where values are rendered at 18pt; timezone display name (e.g. `"Central Time - Chicago"`) appears on the same line as the time range in the same font
13. date uses the format `"Wed Apr 8, 2026"`
14. location wraps within `COLUMN_WIDTH`; block is omitted if location is absent
15. 0.5 inches of white space separates each item block
16. description raw text is cleaned: `<br>` tags become newlines, all other HTML/markdown tags are stripped
17. description is rendered at fixed 12pt and wraps within `COLUMN_WIDTH`; truncates with `…` if it exceeds the available space above the bottom margin
18. `.pdf` files in `output_dir` older than `prune_age_days` are deleted before each new document is generated
19. `.pdf` files newer than `prune_age_days` are left untouched
20. a pruning error (e.g. permission denied on one file) does not prevent document generation from completing

---

## Test Plan
1. enqueue returns immediately
2. PDF file created at expected path in `tmp_path`
3. `document_complete` callback includes correct path
4. file naming is correct for various event titles (special characters, empty summary)
5. multiple event jobs for one request all complete
6. cancellation before start → `document_failed("Cancelled")`
7. cancellation after generation but before callback → `document_failed("Cancelled")`
8. invalid event (missing `id`) → `document_failed` with error
9. injected PDF generator raises → `document_failed` with error
10. shutdown
11. `.pdf` files with mtime older than `prune_age_days` are removed before new documents are written
12. `.pdf` files with mtime within `prune_age_days` are not removed
13. a deletion error on one old file does not abort document creation
