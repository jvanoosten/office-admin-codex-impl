from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

import office_admin.api as api_module
from office_admin.admin import OfficeAdminQueueFullError


class DummyWorker:
    def cancel_request(self, request_id: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@dataclass
class FakeApiOfficeAdmin:
    calendar_worker: object
    document_worker: object
    printer_worker: object
    mail_worker: object
    submitted_dates: list[str] = field(default_factory=list)
    queue_full: bool = False
    statuses: dict[str, dict] = field(
        default_factory=lambda: {
            "known": {
                "request_id": "known",
                "task_type": "PRINT_CALENDAR_EVENTS",
                "status": "RUNNING",
                "stage": "CREATING_EVENT_PDFS",
                "selected_date": "2026-04-21",
                "calendar_event_count": 2,
                "events_retrieved": True,
                "cancel_requested": False,
                "errors": [],
                "created_at": "2026-04-20T00:00:00+00:00",
                "updated_at": "2026-04-20T00:00:00+00:00",
                "documents_expected": 2,
                "documents_completed": 1,
                "documents_failed": 0,
                "prints_expected": 0,
                "prints_completed": 0,
                "prints_failed": 0,
                "document_paths": ["/tmp/one.pdf"],
                "emails_expected": 0,
                "emails_completed": 0,
                "emails_skipped": 0,
                "emails_failed": 0,
                "draft_ids": [],
                "skipped_event_ids": [],
                "calendar_events": [],
            }
        }
    )

    def submit_print_calendar_events(self, selected_date: str) -> str:
        if self.queue_full:
            raise OfficeAdminQueueFullError
        self.submitted_dates.append(selected_date)
        return "generated-id"

    def submit_send_email_notifications(self, selected_date: str) -> str:
        if self.queue_full:
            raise OfficeAdminQueueFullError
        self.submitted_dates.append(selected_date)
        return "generated-email-id"

    def get_status(self, request_id: str) -> dict:
        return self.statuses.get(request_id, {"status": "UNKNOWN", "request_id": request_id})

    def cancel_request(self, request_id: str) -> dict:
        if request_id not in self.statuses:
            return {"status": "UNKNOWN", "request_id": request_id}
        cancelled = dict(self.statuses[request_id])
        cancelled["status"] = "CANCEL_REQUESTED"
        cancelled["cancel_requested"] = True
        return cancelled

    def list_tasks(self) -> list[dict]:
        return list(self.statuses.values())

    async def shutdown(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    created: list[FakeApiOfficeAdmin] = []

    def office_admin_factory(calendar_worker, document_worker, printer_worker, mail_worker):
        admin = FakeApiOfficeAdmin(calendar_worker, document_worker, printer_worker, mail_worker)
        created.append(admin)
        return admin

    monkeypatch.setattr(api_module, "CalendarWorker", DummyWorker)
    monkeypatch.setattr(api_module, "DocumentWorker", DummyWorker)
    monkeypatch.setattr(api_module, "PrinterWorker", DummyWorker)
    monkeypatch.setattr(api_module, "MailWorker", DummyWorker)
    monkeypatch.setattr(api_module, "OfficeAdmin", office_admin_factory)

    with TestClient(api_module.app) as test_client:
        test_client.fake_office_admin = created[0]
        yield test_client


def test_submit_print_calendar_events(client: TestClient) -> None:
    response = client.post("/api/office/print-calendar-events", json={"selected_date": "2026-04-21"})

    assert response.status_code == 202
    assert response.json() == {"request_id": "generated-id"}
    assert client.fake_office_admin.submitted_dates == ["2026-04-21"]


def test_submit_print_calendar_events_invalid_date(client: TestClient) -> None:
    response = client.post("/api/office/print-calendar-events", json={"selected_date": "04/21/2026"})
    assert response.status_code == 422


def test_submit_print_calendar_events_queue_full(client: TestClient) -> None:
    client.fake_office_admin.queue_full = True
    response = client.post("/api/office/print-calendar-events", json={"selected_date": "2026-04-21"})
    assert response.status_code == 429


def test_submit_send_email_notifications(client: TestClient) -> None:
    response = client.post("/api/office/send-email-notifications", json={"selected_date": "2026-04-22"})
    assert response.status_code == 202
    assert response.json() == {"request_id": "generated-email-id"}
    assert client.fake_office_admin.submitted_dates == ["2026-04-22"]


def test_submit_send_email_notifications_invalid_date(client: TestClient) -> None:
    response = client.post("/api/office/send-email-notifications", json={"selected_date": "04/22/2026"})
    assert response.status_code == 422


def test_submit_send_email_notifications_queue_full(client: TestClient) -> None:
    client.fake_office_admin.queue_full = True
    response = client.post("/api/office/send-email-notifications", json={"selected_date": "2026-04-22"})
    assert response.status_code == 429


def test_get_status_success(client: TestClient) -> None:
    response = client.get("/api/office/status/known")
    assert response.status_code == 200
    assert response.json()["documents_completed"] == 1


def test_get_status_unknown_returns_404_payload(client: TestClient) -> None:
    response = client.get("/api/office/status/missing")
    assert response.status_code == 404
    assert response.json() == {"status": "UNKNOWN", "request_id": "missing"}


def test_cancel_success(client: TestClient) -> None:
    response = client.post("/api/office/cancel/known")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCEL_REQUESTED"


def test_cancel_unknown_returns_404_payload(client: TestClient) -> None:
    response = client.post("/api/office/cancel/missing")
    assert response.status_code == 404
    assert response.json() == {"status": "UNKNOWN", "request_id": "missing"}


def test_list_tasks_returns_status_summaries(client: TestClient) -> None:
    response = client.get("/api/office/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_root_page_loads(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Print Calendar Events" in response.text
    assert "Send Email Notifications" in response.text
