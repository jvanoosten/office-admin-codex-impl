from __future__ import annotations

from collections import deque
from typing import Any


class FakeCalendarWorker:
    def __init__(self) -> None:
        self.requests: deque[tuple[Any, str, str]] = deque()
        self.cancelled: list[str] = []

    def get_events_for_date(self, office_admin_ref: Any, request_id: str, selected_date: str) -> None:
        self.requests.append((office_admin_ref, request_id, selected_date))

    def cancel_request(self, request_id: str) -> None:
        self.cancelled.append(request_id)

    async def shutdown(self) -> None:
        return None


class FakeDocumentWorker:
    def __init__(self) -> None:
        self.requests: deque[tuple[Any, str, dict[str, Any]]] = deque()
        self.cancelled: list[str] = []

    def create_event_document(self, office_admin_ref: Any, request_id: str, event: dict[str, Any]) -> None:
        self.requests.append((office_admin_ref, request_id, event))

    def cancel_request(self, request_id: str) -> None:
        self.cancelled.append(request_id)

    async def shutdown(self) -> None:
        return None


class PassiveWorker:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_request(self, request_id: str) -> None:
        self.cancelled.append(request_id)

    async def shutdown(self) -> None:
        return None
