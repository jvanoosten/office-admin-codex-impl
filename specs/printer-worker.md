# Feature: Printer Worker

## Status
Final

## Version
v1.0

## Summary
Implement a `PrinterWorker` that receives PDF file paths and prints them asynchronously, reporting progress back to OfficeAdmin via callbacks.

---

## Problem
Printer integration must not live in OfficeAdmin or FastAPI. PrinterWorker isolates print-submission behavior and provides a clean async interface.

---

## Goals
1. Provide a `PrinterWorker` class using asyncio
2. Accept asynchronous print requests via asyncio.Queue
3. Submit PDF files to the printer subsystem (in executor)
4. Report completion and failure back to OfficeAdmin
5. Support cancellation

---

## Non-Goals
- PDF generation
- calendar retrieval
- orchestration logic
- UI rendering
- printer selection UI (v1 targets system default printer)

---

## Constructor

```python
def __init__(
    self,
    print_adapter=None,
) -> None
```

Initializes:
- `asyncio.Queue()` for work items
- background `asyncio.Task` running the worker loop
- `dict[str, bool]` for cancellation state per request ID
- `print_adapter`: injectable callable for print submission (for testing)

If `print_adapter` is `None`, the default system printer adapter is used.

---

## Public Methods

### `print_document(office_admin_ref, request_id, event_id, document_path) -> None`

Enqueues a print job for one generated document.

Behavior:
1. validate that `document_path` is a non-empty string
2. create work item: `{office_admin_ref, request_id, event_id, document_path}`
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
1. check `cancelled[request_id]`; if True: call `await office_admin_ref.print_failed(request_id, event_id, "Cancelled")`; return
2. verify `document_path` exists on disk; if not: call `await office_admin_ref.print_failed(request_id, event_id, f"File not found: {document_path}")`; return
3. run print submission in executor:
   ```python
   await loop.run_in_executor(None, self._print_adapter, document_path)
   ```
4. check `cancelled[request_id]` again; if True: call `await office_admin_ref.print_failed(request_id, event_id, "Cancelled")`; return
5. call `await office_admin_ref.print_complete(request_id, event_id, document_path)`

On any exception during print submission:
- call `await office_admin_ref.print_failed(request_id, event_id, str(exception))`

---

## Print Adapter

The print adapter is a plain synchronous callable:

```python
def print_adapter(document_path: str) -> None:
    # submits the file to the printer; raises on failure
```

Default implementation: uses the `subprocess` module to invoke the system print command (e.g., `lp` on macOS/Linux).

For testability, the adapter must be injectable via the constructor. The test fake adapter:
- records calls (path, timestamps)
- raises on demand (to simulate print failure)
- never sends jobs to a real printer

---

## Cancellation Notes

Once a print job has been submitted to the OS printer subsystem, it cannot be reliably recalled. The cancellation check after submission is therefore best-effort:
- if the OS accepts the job and the cancellation check fires: `print_failed("Cancelled")` is reported but the physical print may still occur
- this behavior is documented and acceptable in v1

---

## Acceptance Criteria
1. work requests are enqueued and return immediately
2. `print_complete` is reported to OfficeAdmin on success
3. `print_failed` is reported on any exception
4. `print_failed("Cancelled")` is reported when cancelled before print starts
5. missing file → `print_failed` with clear error message
6. print adapter is injectable for testing
7. shutdown completes without hanging

---

## Test Plan
1. enqueue returns immediately
2. successful print → `print_complete` callback (fake adapter)
3. fake adapter raises → `print_failed` callback
4. cancellation before submission → `print_failed("Cancelled")`
5. missing `document_path` → `print_failed` with file-not-found error
6. empty `document_path` string → rejected at enqueue
7. shutdown
