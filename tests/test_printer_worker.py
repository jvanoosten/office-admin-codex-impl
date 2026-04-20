from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from office_admin.workers import PrinterWorker


class DummyOfficeAdmin:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    async def print_complete(self, request_id: str, event_id: str, document_path: str) -> None:
        self.completed.append((request_id, event_id, document_path))

    async def print_failed(self, request_id: str, event_id: str, error_text: str) -> None:
        self.failed.append((request_id, event_id, error_text))


@pytest.mark.asyncio
async def test_printer_worker_successful_print(tmp_path: Path) -> None:
    printed: list[str] = []
    document = tmp_path / "one.pdf"
    document.write_bytes(b"%PDF")

    worker = PrinterWorker(print_adapter=lambda path: printed.append(path))
    office_admin = DummyOfficeAdmin()
    worker.print_document(office_admin, "req-1", "evt-1", str(document))
    await asyncio.sleep(0.1)

    assert printed == [str(document)]
    assert office_admin.completed == [("req-1", "evt-1", str(document))]
    assert office_admin.failed == []
    await worker.shutdown()


@pytest.mark.asyncio
async def test_printer_worker_reports_adapter_error(tmp_path: Path) -> None:
    document = tmp_path / "one.pdf"
    document.write_bytes(b"%PDF")

    def broken(path: str) -> None:
        raise RuntimeError("printer error")

    worker = PrinterWorker(print_adapter=broken)
    office_admin = DummyOfficeAdmin()
    worker.print_document(office_admin, "req-1", "evt-1", str(document))
    await asyncio.sleep(0.1)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "printer error")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_printer_worker_reports_cancelled_before_submission(tmp_path: Path) -> None:
    document = tmp_path / "one.pdf"
    document.write_bytes(b"%PDF")

    worker = PrinterWorker(print_adapter=lambda path: None)
    office_admin = DummyOfficeAdmin()
    worker.cancel_request("req-1")
    worker.print_document(office_admin, "req-1", "evt-1", str(document))
    await asyncio.sleep(0.05)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_printer_worker_reports_cancelled_after_submission(tmp_path: Path) -> None:
    started = threading.Event()
    document = tmp_path / "one.pdf"
    document.write_bytes(b"%PDF")

    def slow(path: str) -> None:
        started.set()
        time.sleep(0.1)

    worker = PrinterWorker(print_adapter=slow)
    office_admin = DummyOfficeAdmin()
    worker.print_document(office_admin, "req-1", "evt-1", str(document))
    await asyncio.get_running_loop().run_in_executor(None, started.wait)
    worker.cancel_request("req-1")
    await asyncio.sleep(0.2)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_printer_worker_reports_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    worker = PrinterWorker(print_adapter=lambda path: None)
    office_admin = DummyOfficeAdmin()
    worker.print_document(office_admin, "req-1", "evt-1", str(missing))
    await asyncio.sleep(0.05)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", f"File not found: {missing}")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_printer_worker_rejects_empty_path() -> None:
    worker = PrinterWorker(print_adapter=lambda path: None)
    try:
        with pytest.raises(ValueError):
            worker.print_document(DummyOfficeAdmin(), "req-1", "evt-1", "")
    finally:
        await worker.shutdown()
