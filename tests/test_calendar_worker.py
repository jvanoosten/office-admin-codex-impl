from __future__ import annotations

import asyncio
from typing import Any

import pytest

from office_admin.workers import CalendarWorker


class DummyOfficeAdmin:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.failed: list[tuple[str, str, str]] = []

    async def calendar_events_complete(self, request_id: str, selected_date: str, events: list[dict[str, Any]]) -> None:
        self.completed.append((request_id, selected_date, events))

    async def calendar_events_failed(self, request_id: str, selected_date: str, error_text: str) -> None:
        self.failed.append((request_id, selected_date, error_text))


class FakeExecutable:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    def execute(self) -> dict[str, Any]:
        return {"items": self._items}


class FakeEventsResource:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> FakeExecutable:
        self.calls.append(kwargs)
        return FakeExecutable(self.items)


class FakeService:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.events_resource = FakeEventsResource(items)

    def events(self) -> FakeEventsResource:
        return self.events_resource


@pytest.mark.asyncio
async def test_worker_filters_and_normalizes_events() -> None:
    items = [
        {
            "id": "evt-1",
            "summary": "Printable",
            "start": {"dateTime": "2026-04-21T09:00:00-05:00", "timeZone": "America/Chicago"},
            "end": {"dateTime": "2026-04-21T10:00:00-05:00"},
            "colorId": "7",
        },
        {
            "id": "evt-2",
            "summary": "Too early",
            "start": {"dateTime": "2026-04-21T07:30:00-05:00"},
            "end": {"dateTime": "2026-04-21T08:30:00-05:00"},
        },
        {
            "id": "evt-3",
            "summary": "All day",
            "start": {"date": "2026-04-21"},
            "end": {"date": "2026-04-22"},
        },
    ]
    service = FakeService(items)
    worker = CalendarWorker(service_factory=lambda: service)
    office_admin = DummyOfficeAdmin()

    worker.get_events_for_date(office_admin, "req-1", "2026-04-21")
    await asyncio.sleep(0.05)

    assert office_admin.failed == []
    assert len(office_admin.completed) == 1
    _, _, events = office_admin.completed[0]
    assert [event["id"] for event in events] == ["evt-1"]
    assert events[0]["colorId"] == "7"

    await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_reports_cancelled_before_processing() -> None:
    service = FakeService([])
    worker = CalendarWorker(service_factory=lambda: service)
    office_admin = DummyOfficeAdmin()

    worker.cancel_request("req-1")
    worker.get_events_for_date(office_admin, "req-1", "2026-04-21")
    await asyncio.sleep(0.05)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "2026-04-21", "Cancelled")]

    await worker.shutdown()


def test_fetch_raw_events_uses_local_midnight_boundaries() -> None:
    service = FakeService([])
    worker = CalendarWorker(service_factory=lambda: service)

    raw_items = worker._fetch_raw_events(service, "2026-04-21")
    assert raw_items == []

    kwargs = service.events_resource.calls[0]
    assert kwargs["calendarId"] == "primary"
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"
    assert kwargs["timeMin"].endswith(":00")
    assert kwargs["timeMax"].endswith(":00")
    assert "T00:00:00" in kwargs["timeMin"]
    assert "T00:00:00" in kwargs["timeMax"]
