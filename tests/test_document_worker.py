from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from office_admin.workers import DocumentWorker


class DummyOfficeAdmin:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    async def document_complete(self, request_id: str, event_id: str, document_path: str) -> None:
        self.completed.append((request_id, event_id, document_path))

    async def document_failed(self, request_id: str, event_id: str, error_text: str) -> None:
        self.failed.append((request_id, event_id, error_text))


@pytest.mark.asyncio
async def test_create_event_document_generates_pdf_and_reports_path(tmp_path: Path) -> None:
    worker = DocumentWorker(output_dir=str(tmp_path))
    office_admin = DummyOfficeAdmin()
    event = {
        "id": "Event 123!",
        "summary": "Team Standup / Morning",
        "start": "2026-04-21T09:00:00-05:00",
        "end": "2026-04-21T10:00:00-05:00",
        "timezone": "America/Chicago",
        "description": "Daily sync",
        "location": "Room 1",
        "colorId": "7",
    }

    worker.create_event_document(office_admin, "12345678-aaaa-bbbb-cccc-1234567890ab", event)
    await asyncio.sleep(0.35)

    assert office_admin.failed == []
    assert len(office_admin.completed) == 1
    _, event_id, document_path = office_admin.completed[0]
    assert event_id == "Event 123!"
    assert Path(document_path).exists()
    assert Path(document_path).suffix == ".pdf"
    assert "12345678_event-123_team-standup-morning.pdf" in document_path
    await worker.shutdown()


@pytest.mark.asyncio
async def test_create_event_document_requires_event_id(tmp_path: Path) -> None:
    worker = DocumentWorker(output_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError):
            worker.create_event_document(DummyOfficeAdmin(), "req-1", {"summary": "No id"})
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_document_worker_prunes_stale_pdfs(tmp_path: Path) -> None:
    stale = tmp_path / "stale.pdf"
    stale.write_bytes(b"old")
    old_mtime = time.time() - (8 * 24 * 60 * 60)
    os.utime(stale, (old_mtime, old_mtime))

    worker = DocumentWorker(output_dir=str(tmp_path), prune_age_days=7)
    office_admin = DummyOfficeAdmin()
    event = {"id": "evt-1", "summary": "Fresh", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"}
    worker.create_event_document(office_admin, "req-1", event)
    await asyncio.sleep(0.35)

    assert not stale.exists()
    assert len(office_admin.completed) == 1
    await worker.shutdown()


@pytest.mark.asyncio
async def test_document_worker_reports_cancellation_before_processing(tmp_path: Path) -> None:
    worker = DocumentWorker(output_dir=str(tmp_path))
    office_admin = DummyOfficeAdmin()
    worker.cancel_request("req-1")
    worker.create_event_document(office_admin, "req-1", {"id": "evt-1", "summary": "One"})
    await asyncio.sleep(0.05)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_document_worker_reports_cancellation_after_generation(tmp_path: Path) -> None:
    started = threading.Event()

    def slow_generator(event: dict[str, Any], output_path: Path) -> None:
        started.set()
        time.sleep(0.1)
        output_path.write_bytes(b"%PDF-1.4")

    worker = DocumentWorker(output_dir=str(tmp_path), pdf_generator=slow_generator)
    office_admin = DummyOfficeAdmin()
    worker.create_event_document(office_admin, "req-1", {"id": "evt-1", "summary": "One"})
    await asyncio.get_running_loop().run_in_executor(None, started.wait)
    worker.cancel_request("req-1")
    await asyncio.sleep(0.2)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_document_worker_reports_generation_error(tmp_path: Path) -> None:
    def broken_generator(event: dict[str, Any], output_path: Path) -> None:
        raise RuntimeError("pdf failure")

    worker = DocumentWorker(output_dir=str(tmp_path), pdf_generator=broken_generator)
    office_admin = DummyOfficeAdmin()
    worker.create_event_document(office_admin, "req-1", {"id": "evt-1", "summary": "One"})
    await asyncio.sleep(0.1)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "pdf failure")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_document_worker_handles_multiple_jobs(tmp_path: Path) -> None:
    worker = DocumentWorker(output_dir=str(tmp_path))
    office_admin = DummyOfficeAdmin()
    worker.create_event_document(office_admin, "req-1", {"id": "evt-1", "summary": "One"})
    worker.create_event_document(office_admin, "req-1", {"id": "evt-2", "summary": "Two"})
    await asyncio.sleep(0.45)

    assert len(office_admin.completed) == 2
    assert all(Path(path).exists() for _, _, path in office_admin.completed)
    await worker.shutdown()
