from __future__ import annotations

import asyncio

import pytest

from office_admin.admin import OfficeAdmin, OfficeAdminQueueFullError
from office_admin.models import CANCELLED, COMPLETED, ERROR, GETTING_CALENDAR_EVENTS, PRINT_CALENDAR_EVENTS

from tests.fakes import FakeCalendarWorker, PassiveWorker


@pytest.fixture
async def admin() -> OfficeAdmin:
    instance = OfficeAdmin(FakeCalendarWorker(), PassiveWorker(), PassiveWorker(), PassiveWorker())
    yield instance
    await instance.shutdown()


@pytest.mark.asyncio
async def test_submit_print_calendar_events_transitions_to_running(admin: OfficeAdmin) -> None:
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    status = admin.get_status(request_id)
    assert status["task_type"] == PRINT_CALENDAR_EVENTS
    assert status["status"] == "RUNNING"
    assert status["stage"] == GETTING_CALENDAR_EVENTS


@pytest.mark.asyncio
async def test_calendar_completion_records_events_and_completes(admin: OfficeAdmin) -> None:
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    await admin.calendar_events_complete(
        request_id,
        "2026-04-21",
        [{"id": "evt-1", "summary": "Team Standup", "start": "2026-04-21T09:00:00-05:00", "end": "2026-04-21T10:00:00-05:00"}],
    )

    status = admin.get_status(request_id)
    assert status["status"] == COMPLETED
    assert status["stage"] == COMPLETED
    assert status["calendar_event_count"] == 1
    assert status["calendar_events"][0]["id"] == "evt-1"


@pytest.mark.asyncio
async def test_calendar_failure_marks_error(admin: OfficeAdmin) -> None:
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    await admin.calendar_events_failed(request_id, "2026-04-21", "Calendar API error")

    status = admin.get_status(request_id)
    assert status["status"] == ERROR
    assert status["stage"] == ERROR
    assert status["errors"] == ["Calendar API error"]


@pytest.mark.asyncio
async def test_cancel_request_propagates_and_finishes_cancelled(admin: OfficeAdmin) -> None:
    request_id = admin.submit_print_calendar_events("2026-04-21")
    await asyncio.sleep(0)

    cancelled = admin.cancel_request(request_id)
    assert cancelled["status"] == "CANCEL_REQUESTED"

    await admin.calendar_events_failed(request_id, "2026-04-21", "Cancelled")

    status = admin.get_status(request_id)
    assert status["status"] == CANCELLED
    assert status["stage"] == CANCELLED


@pytest.mark.asyncio
async def test_queue_full_raises() -> None:
    calendar_worker = FakeCalendarWorker()
    admin = OfficeAdmin(calendar_worker, PassiveWorker(), PassiveWorker(), PassiveWorker())
    try:
        for _ in range(10):
            admin.submit_print_calendar_events("2026-04-21")
        with pytest.raises(OfficeAdminQueueFullError):
            admin.submit_print_calendar_events("2026-04-22")
    finally:
        await admin.shutdown()
