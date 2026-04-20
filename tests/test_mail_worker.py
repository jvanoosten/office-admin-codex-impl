from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import Any

import pytest

from office_admin.workers import MailWorker


class DummyOfficeAdmin:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str, str]] = []
        self.skipped: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    async def email_draft_complete(self, request_id: str, event_id: str, draft_id: str) -> None:
        self.completed.append((request_id, event_id, draft_id))

    async def email_draft_skipped(self, request_id: str, event_id: str) -> None:
        self.skipped.append((request_id, event_id))

    async def email_draft_failed(self, request_id: str, event_id: str, error_text: str) -> None:
        self.failed.append((request_id, event_id, error_text))


class FakeDraftCreate:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response or {"id": "draft-1"}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any):
        self.calls.append(kwargs)

        class ExecuteWrapper:
            def __init__(self, parent: FakeDraftCreate) -> None:
                self.parent = parent

            def execute(self) -> dict[str, Any]:
                if self.parent.error:
                    raise self.parent.error
                return self.parent.response

        return ExecuteWrapper(self)


class FakeGmailUsers:
    def __init__(self, drafts_service: FakeDraftCreate) -> None:
        self._drafts_service = drafts_service

    def drafts(self) -> FakeDraftCreate:
        return self._drafts_service


class FakeGmailService:
    def __init__(self, drafts_service: FakeDraftCreate) -> None:
        self._users = FakeGmailUsers(drafts_service)

    def users(self) -> FakeGmailUsers:
        return self._users


def decode_raw_message(raw: str) -> str:
    return base64.urlsafe_b64decode(raw.encode("utf-8")).decode("utf-8")


def test_extract_recipients_variants() -> None:
    assert MailWorker._extract_recipients("contact me at a@example.com") == ["a@example.com"]
    assert MailWorker._extract_recipients("a@example.com and b@example.com") == ["a@example.com", "b@example.com"]
    assert MailWorker._extract_recipients("a@example.com and a@example.com") == ["a@example.com"]
    assert MailWorker._extract_recipients("no recipients here") == []
    assert MailWorker._extract_recipients(None) == []


@pytest.mark.asyncio
async def test_compose_draft_body_uses_template_placeholders() -> None:
    worker = MailWorker(service_factory=lambda: FakeGmailService(FakeDraftCreate()))
    try:
        raw = worker._compose_draft_body(
            {
                "id": "evt-1",
                "start": "2026-04-21T09:00:00-05:00",
                "end": "2026-04-21T10:00:00-05:00",
                "location": "Main Office",
            },
            ["a@example.com"],
            template="Subject Line\nDate: {date}\nTime: {time}\nLocation: {location}\n",
        )
        message = decode_raw_message(raw)
        assert "Subject: Subject Line" in message
        assert "{date}" not in message
        assert "{time}" not in message
        assert "{location}" not in message
        assert "Main Office" in message
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_event_with_recipients_creates_gmail_draft() -> None:
    drafts_service = FakeDraftCreate({"id": "draft-123"})
    worker = MailWorker(service_factory=lambda: FakeGmailService(drafts_service))
    office_admin = DummyOfficeAdmin()
    worker.create_email_draft(
        office_admin,
        "req-1",
        {
            "id": "evt-1",
            "description": "Reach me at a@example.com",
            "start": "2026-04-21T09:00:00-05:00",
            "end": "2026-04-21T10:00:00-05:00",
            "location": "Main Office",
        },
    )
    await asyncio.sleep(0.1)

    assert office_admin.completed == [("req-1", "evt-1", "draft-123")]
    assert office_admin.skipped == []
    assert office_admin.failed == []
    assert drafts_service.calls[0]["userId"] == "me"
    await worker.shutdown()


@pytest.mark.asyncio
async def test_event_without_recipients_is_skipped() -> None:
    drafts_service = FakeDraftCreate()
    worker = MailWorker(service_factory=lambda: FakeGmailService(drafts_service))
    office_admin = DummyOfficeAdmin()
    worker.create_email_draft(office_admin, "req-1", {"id": "evt-1", "description": "No email here"})
    await asyncio.sleep(0.05)

    assert office_admin.completed == []
    assert office_admin.skipped == [("req-1", "evt-1")]
    assert office_admin.failed == []
    assert drafts_service.calls == []
    await worker.shutdown()


@pytest.mark.asyncio
async def test_gmail_api_failure_reports_failed() -> None:
    worker = MailWorker(service_factory=lambda: FakeGmailService(FakeDraftCreate(error=RuntimeError("gmail down"))))
    office_admin = DummyOfficeAdmin()
    worker.create_email_draft(office_admin, "req-1", {"id": "evt-1", "description": "a@example.com"})
    await asyncio.sleep(0.1)

    assert office_admin.completed == []
    assert office_admin.skipped == []
    assert office_admin.failed == [("req-1", "evt-1", "gmail down")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_cancellation_before_processing_reports_failed() -> None:
    worker = MailWorker(service_factory=lambda: FakeGmailService(FakeDraftCreate()))
    office_admin = DummyOfficeAdmin()
    worker.cancel_request("req-1")
    worker.create_email_draft(office_admin, "req-1", {"id": "evt-1", "description": "a@example.com"})
    await asyncio.sleep(0.05)

    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_cancellation_after_recipient_extraction_reports_failed() -> None:
    started = threading.Event()

    class SlowDraftCreate(FakeDraftCreate):
        def create(self, **kwargs: Any):
            started.set()
            time.sleep(0.1)
            return super().create(**kwargs)

    worker = MailWorker(service_factory=lambda: FakeGmailService(SlowDraftCreate()))
    office_admin = DummyOfficeAdmin()
    worker.create_email_draft(office_admin, "req-1", {"id": "evt-1", "description": "a@example.com"})
    await asyncio.get_running_loop().run_in_executor(None, started.wait)
    worker.cancel_request("req-1")
    await asyncio.sleep(0.2)

    assert office_admin.completed == []
    assert office_admin.failed == [("req-1", "evt-1", "Cancelled")]
    await worker.shutdown()


@pytest.mark.asyncio
async def test_oauth_failure_reports_failed() -> None:
    worker = MailWorker(service_factory=lambda: (_ for _ in ()).throw(RuntimeError("oauth failed")))
    office_admin = DummyOfficeAdmin()
    worker.create_email_draft(office_admin, "req-1", {"id": "evt-1", "description": "a@example.com"})
    await asyncio.sleep(0.1)

    assert office_admin.failed == [("req-1", "evt-1", "oauth failed")]
    await worker.shutdown()
