from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_submit_print_calendar_events(client: TestClient) -> None:
    response = client.post("/api/office/print-calendar-events", json={"selected_date": "2026-04-21"})

    assert response.status_code == 202
    assert "request_id" in response.json()


def test_submit_print_calendar_events_invalid_date(client: TestClient) -> None:
    response = client.post("/api/office/print-calendar-events", json={"selected_date": "04/21/2026"})

    assert response.status_code == 422


def test_get_status_unknown_returns_404_payload(client: TestClient) -> None:
    response = client.get("/api/office/status/missing")

    assert response.status_code == 404
    assert response.json() == {"status": "UNKNOWN", "request_id": "missing"}


def test_cancel_unknown_returns_404_payload(client: TestClient) -> None:
    response = client.post("/api/office/cancel/missing")

    assert response.status_code == 404
    assert response.json() == {"status": "UNKNOWN", "request_id": "missing"}


def test_root_page_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Print Calendar Events" in response.text
