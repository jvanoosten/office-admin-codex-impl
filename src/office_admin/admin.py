from __future__ import annotations

import asyncio
import uuid
from typing import Any

from office_admin.models import (
    CANCELLED,
    CANCEL_REQUESTED,
    COMPLETED,
    CREATING_EVENT_PDFS,
    ERROR,
    GETTING_CALENDAR_EVENTS,
    PRINT_CALENDAR_EVENTS,
    RUNNING,
    SEND_EMAIL_NOTIFICATIONS,
    TERMINAL_STATUSES,
    UNKNOWN,
    OfficeAdminWorkItem,
    make_task_entry,
    utc_now_iso,
)


class OfficeAdminQueueFullError(Exception):
    pass


class OfficeAdmin:
    def __init__(self, calendar_worker: Any, document_worker: Any, printer_worker: Any, mail_worker: Any) -> None:
        self._calendar_worker = calendar_worker
        self._document_worker = document_worker
        self._printer_worker = printer_worker
        self._mail_worker = mail_worker
        self._queue: asyncio.Queue[OfficeAdminWorkItem | None] = asyncio.Queue(maxsize=10)
        self._task_store: dict[str, dict[str, Any]] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    def submit_print_calendar_events(self, selected_date: str) -> str:
        return self._submit_task(PRINT_CALENDAR_EVENTS, selected_date)

    def submit_send_email_notifications(self, selected_date: str) -> str:
        return self._submit_task(SEND_EMAIL_NOTIFICATIONS, selected_date)

    def get_status(self, request_id: str) -> dict[str, Any]:
        task = self._task_store.get(request_id)
        if task is None:
            return {"status": UNKNOWN, "request_id": request_id}
        return dict(task)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [dict(task) for task in sorted(self._task_store.values(), key=lambda item: item["created_at"], reverse=True)]

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        task = self._task_store.get(request_id)
        if task is None:
            return {"status": UNKNOWN, "request_id": request_id}
        if task["status"] in TERMINAL_STATUSES:
            return dict(task)

        task["cancel_requested"] = True
        task["status"] = CANCEL_REQUESTED
        self._touch(task)
        self._calendar_worker.cancel_request(request_id)
        self._document_worker.cancel_request(request_id)
        self._printer_worker.cancel_request(request_id)
        self._mail_worker.cancel_request(request_id)
        return dict(task)

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def calendar_events_complete(self, request_id: str, selected_date: str, events: list[dict[str, Any]]) -> None:
        task = self._task_store.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return

        task["events_retrieved"] = True
        task["calendar_event_count"] = len(events)
        task["calendar_events"] = list(events)
        self._touch(task)

        if task["cancel_requested"]:
            task["status"] = CANCELLED
            task["stage"] = CANCELLED
            self._touch(task)
            return

        if not events:
            task["status"] = COMPLETED
            task["stage"] = COMPLETED
            self._touch(task)
            return

        if task["task_type"] == PRINT_CALENDAR_EVENTS:
            task["status"] = RUNNING
            task["stage"] = CREATING_EVENT_PDFS
            task["documents_expected"] = len(events)
            task["documents_completed"] = 0
            task["documents_failed"] = 0
            task["document_paths"] = []
            self._touch(task)
            for event in events:
                self._document_worker.create_event_document(self, request_id, event)
            return

        task["status"] = COMPLETED
        task["stage"] = COMPLETED
        self._touch(task)

    async def calendar_events_failed(self, request_id: str, selected_date: str, error_text: str) -> None:
        task = self._task_store.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return

        task["events_retrieved"] = False
        if task["cancel_requested"] and error_text == "Cancelled":
            task["status"] = CANCELLED
            task["stage"] = CANCELLED
        else:
            task["status"] = ERROR
            task["stage"] = ERROR
            task["errors"].append(error_text)
            self._calendar_worker.cancel_request(request_id)
            self._document_worker.cancel_request(request_id)
            self._printer_worker.cancel_request(request_id)
            self._mail_worker.cancel_request(request_id)
        self._touch(task)

    async def document_complete(self, request_id: str, event_id: str, document_path: str) -> None:
        task = self._task_store.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return

        task["documents_completed"] += 1
        task["document_paths"].append(document_path)

        if task["cancel_requested"]:
            if self._document_work_is_finished(task):
                task["status"] = CANCELLED
                task["stage"] = CANCELLED
            self._touch(task)
            return

        if task["documents_completed"] == task["documents_expected"]:
            task["status"] = COMPLETED
            task["stage"] = COMPLETED
        self._touch(task)

    async def document_failed(self, request_id: str, event_id: str, error_text: str) -> None:
        task = self._task_store.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return

        task["documents_failed"] += 1

        if task["cancel_requested"] and error_text == "Cancelled":
            if self._document_work_is_finished(task):
                task["status"] = CANCELLED
                task["stage"] = CANCELLED
            self._touch(task)
            return

        task["status"] = ERROR
        task["stage"] = ERROR
        task["errors"].append(error_text)
        self._calendar_worker.cancel_request(request_id)
        self._document_worker.cancel_request(request_id)
        self._printer_worker.cancel_request(request_id)
        self._mail_worker.cancel_request(request_id)
        self._touch(task)

    def _submit_task(self, task_type: str, selected_date: str) -> str:
        request_id = str(uuid.uuid4())
        item: OfficeAdminWorkItem = {
            "request_id": request_id,
            "task_type": task_type,
            "selected_date": selected_date,
        }
        task = make_task_entry(request_id, task_type, selected_date)

        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise OfficeAdminQueueFullError from exc

        self._task_store[request_id] = task
        return request_id

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: OfficeAdminWorkItem) -> None:
        task = self._task_store.get(item["request_id"])
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        if task["cancel_requested"]:
            task["status"] = CANCELLED
            task["stage"] = CANCELLED
            self._touch(task)
            return

        task["status"] = RUNNING
        task["stage"] = GETTING_CALENDAR_EVENTS
        self._touch(task)
        self._calendar_worker.get_events_for_date(self, item["request_id"], item["selected_date"])

    @staticmethod
    def _touch(task: dict[str, Any]) -> None:
        task["updated_at"] = utc_now_iso()

    @staticmethod
    def _document_work_is_finished(task: dict[str, Any]) -> bool:
        return (task["documents_completed"] + task["documents_failed"]) == task["documents_expected"]
