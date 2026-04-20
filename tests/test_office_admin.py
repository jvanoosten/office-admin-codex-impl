from __future__ import annotations

import asyncio

import pytest

from office_admin.admin import OfficeAdmin, OfficeAdminQueueFullError
from office_admin.models import (
    CANCELLED,
    COMPLETED,
    CREATING_EVENT_PDFS,
    ERROR,
    GETTING_CALENDAR_EVENTS,
    PRINT_CALENDAR_EVENTS,
    PRINTING_EVENT_PDFS,
)
from tests.fakes import FakeCalendarWorker, FakeDocumentWorker, FakePrinterWorker, PassiveWorker


@pytest.fixture
async def admin_context() -> tuple[OfficeAdmin, FakeCalendarWorker, FakeDocumentWorker, FakePrinterWorker, PassiveWorker]:
    calendar_worker = FakeCalendarWorker()
    document_worker = FakeDocumentWorker()
    printer_worker = FakePrinterWorker()
    mail_worker = PassiveWorker()
    admin = OfficeAdmin(calendar_worker, document_worker, printer_worker, mail_worker)
    yield admin, calendar_worker, document_worker, printer_worker, mail_worker
    await admin.shutdown()


@pytest.mark.asyncio
async def test_submit_print_calendar_events_transitions_to_running(admin_context) -> None:
    admin, calendar_worker, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    status = admin.get_status(request_id)
    assert status["task_type"] == PRINT_CALENDAR_EVENTS
    assert status["status"] == "RUNNING"
    assert status["stage"] == GETTING_CALENDAR_EVENTS
    assert calendar_worker.requests[0][1:] == (request_id, "2026-04-21")


@pytest.mark.asyncio
async def test_zero_event_day_completes_immediately(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    await admin.calendar_events_complete(request_id, "2026-04-21", [])

    status = admin.get_status(request_id)
    assert status["status"] == COMPLETED
    assert status["stage"] == COMPLETED
    assert status["calendar_event_count"] == 0


@pytest.mark.asyncio
async def test_calendar_completion_dispatches_document_jobs(admin_context) -> None:
    admin, _, document_worker, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    events = [
        {"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"},
        {"id": "evt-2", "summary": "Two", "start": "2026-04-21T11:00:00-05:00", "end": "2026-04-21T12:00:00-05:00"},
    ]

    await admin.calendar_events_complete(request_id, "2026-04-21", events)

    status = admin.get_status(request_id)
    assert status["status"] == "RUNNING"
    assert status["stage"] == CREATING_EVENT_PDFS
    assert status["documents_expected"] == 2
    assert len(document_worker.requests) == 2


@pytest.mark.asyncio
async def test_document_completion_dispatches_print_jobs(admin_context) -> None:
    admin, _, _, printer_worker, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    events = [
        {"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"},
        {"id": "evt-2", "summary": "Two", "start": "2026-04-21T11:00:00-05:00", "end": "2026-04-21T12:00:00-05:00"},
    ]
    await admin.calendar_events_complete(request_id, "2026-04-21", events)

    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")
    mid_status = admin.get_status(request_id)
    assert mid_status["status"] == "RUNNING"
    assert mid_status["documents_completed"] == 1

    await admin.document_complete(request_id, "evt-2", "/tmp/two.pdf")
    final_status = admin.get_status(request_id)
    assert final_status["status"] == "RUNNING"
    assert final_status["stage"] == PRINTING_EVENT_PDFS
    assert final_status["document_paths"] == ["/tmp/one.pdf", "/tmp/two.pdf"]
    assert final_status["prints_expected"] == 2
    assert len(printer_worker.requests) == 2


@pytest.mark.asyncio
async def test_print_completion_marks_task_completed(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    events = [
        {"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"},
        {"id": "evt-2", "summary": "Two", "start": "2026-04-21T11:00:00-05:00", "end": "2026-04-21T12:00:00-05:00"},
    ]
    await admin.calendar_events_complete(request_id, "2026-04-21", events)
    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")
    await admin.document_complete(request_id, "evt-2", "/tmp/two.pdf")

    await admin.print_complete(request_id, "evt-1", "/tmp/one.pdf")
    mid_status = admin.get_status(request_id)
    assert mid_status["status"] == "RUNNING"
    assert mid_status["prints_completed"] == 1

    await admin.print_complete(request_id, "evt-2", "/tmp/two.pdf")
    final_status = admin.get_status(request_id)
    assert final_status["status"] == COMPLETED
    assert final_status["stage"] == COMPLETED


@pytest.mark.asyncio
async def test_calendar_failure_marks_error(admin_context) -> None:
    admin, calendar_worker, document_worker, printer_worker, mail_worker = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    await admin.calendar_events_failed(request_id, "2026-04-21", "Calendar API error")

    status = admin.get_status(request_id)
    assert status["status"] == ERROR
    assert status["stage"] == ERROR
    assert status["errors"] == ["Calendar API error"]
    assert request_id in calendar_worker.cancelled
    assert request_id in document_worker.cancelled
    assert request_id in printer_worker.cancelled
    assert request_id in mail_worker.cancelled


@pytest.mark.asyncio
async def test_document_failure_marks_error_and_propagates_cancel(admin_context) -> None:
    admin, calendar_worker, document_worker, printer_worker, mail_worker = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    await admin.calendar_events_complete(
        request_id,
        "2026-04-21",
        [{"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"}],
    )

    await admin.document_failed(request_id, "evt-1", "PDF exploded")

    status = admin.get_status(request_id)
    assert status["status"] == ERROR
    assert status["stage"] == ERROR
    assert status["errors"] == ["PDF exploded"]
    assert request_id in calendar_worker.cancelled
    assert request_id in document_worker.cancelled
    assert request_id in printer_worker.cancelled
    assert request_id in mail_worker.cancelled


@pytest.mark.asyncio
async def test_printer_failure_marks_error_and_propagates_cancel(admin_context) -> None:
    admin, calendar_worker, document_worker, printer_worker, mail_worker = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    await admin.calendar_events_complete(
        request_id,
        "2026-04-21",
        [{"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"}],
    )
    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")

    await admin.print_failed(request_id, "evt-1", "Printer jam")

    status = admin.get_status(request_id)
    assert status["status"] == ERROR
    assert status["stage"] == ERROR
    assert status["errors"] == ["Printer jam"]
    assert request_id in calendar_worker.cancelled
    assert request_id in document_worker.cancelled
    assert request_id in printer_worker.cancelled
    assert request_id in mail_worker.cancelled


@pytest.mark.asyncio
async def test_cancel_request_during_calendar_stage_finishes_cancelled(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    cancelled = admin.cancel_request(request_id)
    assert cancelled["status"] == "CANCEL_REQUESTED"

    await admin.calendar_events_failed(request_id, "2026-04-21", "Cancelled")

    status = admin.get_status(request_id)
    assert status["status"] == CANCELLED
    assert status["stage"] == CANCELLED


@pytest.mark.asyncio
async def test_cancel_request_during_document_stage_waits_for_all_callbacks(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    events = [
        {"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"},
        {"id": "evt-2", "summary": "Two", "start": "2026-04-21T11:00:00-05:00", "end": "2026-04-21T12:00:00-05:00"},
    ]
    await admin.calendar_events_complete(request_id, "2026-04-21", events)

    admin.cancel_request(request_id)
    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")
    mid_status = admin.get_status(request_id)
    assert mid_status["status"] == "CANCEL_REQUESTED"

    await admin.document_failed(request_id, "evt-2", "Cancelled")
    final_status = admin.get_status(request_id)
    assert final_status["status"] == CANCELLED
    assert final_status["stage"] == CANCELLED
    assert final_status["documents_completed"] == 1
    assert final_status["documents_failed"] == 1


@pytest.mark.asyncio
async def test_cancel_request_during_print_stage_waits_for_all_callbacks(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    events = [
        {"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"},
        {"id": "evt-2", "summary": "Two", "start": "2026-04-21T11:00:00-05:00", "end": "2026-04-21T12:00:00-05:00"},
    ]
    await admin.calendar_events_complete(request_id, "2026-04-21", events)
    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")
    await admin.document_complete(request_id, "evt-2", "/tmp/two.pdf")

    admin.cancel_request(request_id)
    await admin.print_complete(request_id, "evt-1", "/tmp/one.pdf")
    mid_status = admin.get_status(request_id)
    assert mid_status["status"] == "CANCEL_REQUESTED"

    await admin.print_failed(request_id, "evt-2", "Cancelled")
    final_status = admin.get_status(request_id)
    assert final_status["status"] == CANCELLED
    assert final_status["stage"] == CANCELLED
    assert final_status["prints_completed"] == 1
    assert final_status["prints_failed"] == 1


@pytest.mark.asyncio
async def test_late_callback_after_completion_is_discarded(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    await admin.calendar_events_complete(
        request_id,
        "2026-04-21",
        [{"id": "evt-1", "summary": "One", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"}],
    )
    await admin.document_complete(request_id, "evt-1", "/tmp/one.pdf")
    await admin.print_complete(request_id, "evt-1", "/tmp/one.pdf")
    status_before = admin.get_status(request_id)

    await admin.print_failed(request_id, "evt-1", "Too late")

    assert admin.get_status(request_id) == status_before


@pytest.mark.asyncio
async def test_duplicate_cancel_is_idempotent(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    first = admin.cancel_request(request_id)
    second = admin.cancel_request(request_id)

    assert first["status"] == "CANCEL_REQUESTED"
    assert second["status"] == "CANCEL_REQUESTED"


@pytest.mark.asyncio
async def test_cancel_completed_task_is_no_op(admin_context) -> None:
    admin, _, _, _, _ = admin_context
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)
    await admin.calendar_events_complete(request_id, "2026-04-21", [])

    status_before = admin.get_status(request_id)
    status_after = admin.cancel_request(request_id)

    assert status_after == status_before


@pytest.mark.asyncio
async def test_queue_full_raises() -> None:
    calendar_worker = FakeCalendarWorker()
    admin = OfficeAdmin(calendar_worker, FakeDocumentWorker(), FakePrinterWorker(), PassiveWorker())
    try:
        for _ in range(10):
            admin.submit_print_calendar_events("2026-04-21")
        with pytest.raises(OfficeAdminQueueFullError):
            admin.submit_print_calendar_events("2026-04-22")
    finally:
        await admin.shutdown()
