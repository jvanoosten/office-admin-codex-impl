from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict

from office_admin.models import CalendarEvent

LOGGER = logging.getLogger(__name__)
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class CalendarWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    selected_date: str


class CalendarEventsService(Protocol):
    def list(self, **kwargs: Any) -> Any: ...


class CalendarService(Protocol):
    def events(self) -> CalendarEventsService: ...


class CalendarWorker:
    def __init__(
        self,
        credentials_path: str = "credentials.json",
        token_path: str = "token.json",
        service_factory: Callable[[], CalendarService] | None = None,
    ) -> None:
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._service_factory = service_factory or self._build_service
        self._queue: asyncio.Queue[CalendarWorkItem | None] = asyncio.Queue()
        self._cancelled: dict[str, bool] = {}
        self._service: CalendarService | None = None
        self._worker_task = asyncio.create_task(self._worker_loop())

    def get_events_for_date(self, office_admin_ref: Any, request_id: str, selected_date: str) -> None:
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "selected_date": selected_date,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled[request_id] = True

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: CalendarWorkItem) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        selected_date = item["selected_date"]

        if self._cancelled.get(request_id):
            await office_admin_ref.calendar_events_failed(request_id, selected_date, "Cancelled")
            return

        try:
            service = await self._get_service()
            events = await self._fetch_events(service, selected_date)
        except Exception as exc:  # pragma: no cover - exercised in tests through behavior
            await office_admin_ref.calendar_events_failed(request_id, selected_date, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.calendar_events_failed(request_id, selected_date, "Cancelled")
            return

        await office_admin_ref.calendar_events_complete(request_id, selected_date, events)

    async def _get_service(self) -> CalendarService:
        if self._service is None:
            loop = asyncio.get_running_loop()
            self._service = await loop.run_in_executor(None, self._service_factory)
        return self._service

    async def _fetch_events(self, service: CalendarService, selected_date: str) -> list[CalendarEvent]:
        loop = asyncio.get_running_loop()
        raw_items = await loop.run_in_executor(None, self._fetch_raw_events, service, selected_date)
        events: list[CalendarEvent] = []
        for raw in raw_items:
            if self._is_printable_event(raw):
                events.append(self._normalize_event(raw))
        return events

    def _fetch_raw_events(self, service: CalendarService, selected_date: str) -> list[dict[str, Any]]:
        selected = dt.date.fromisoformat(selected_date)
        next_day = selected + dt.timedelta(days=1)
        local_tz = dt.datetime.now(dt.UTC).astimezone().tzinfo
        time_min = dt.datetime(selected.year, selected.month, selected.day, tzinfo=local_tz).isoformat()
        time_max = dt.datetime(next_day.year, next_day.month, next_day.day, tzinfo=local_tz).isoformat()

        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return list(result.get("items", []))

    @staticmethod
    def _is_printable_event(raw: dict[str, Any]) -> bool:
        start_str = raw.get("start", {}).get("dateTime")
        end_str = raw.get("end", {}).get("dateTime")
        if not start_str or not end_str:
            return False

        try:
            start_local = dt.datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone()
            end_local = dt.datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone()
        except (ValueError, AttributeError):
            LOGGER.warning("Skipping unparsable calendar event", extra={"event_id": raw.get("id")})
            return False

        return start_local.time() >= dt.time(8, 0) and end_local.time() <= dt.time(18, 0)

    @staticmethod
    def _normalize_event(raw: dict[str, Any]) -> CalendarEvent:
        start = raw.get("start", {})
        end = raw.get("end", {})
        return {
            "id": str(raw.get("id", "")),
            "summary": raw.get("summary", "") or "",
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
            "timezone": start.get("timeZone"),
            "location": raw.get("location"),
            "description": raw.get("description"),
            "html_link": raw.get("htmlLink"),
            "status": raw.get("status"),
            "colorId": raw.get("colorId"),
        }

    def _build_service(self) -> CalendarService:
        creds = None
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - depends on optional local setup
            raise RuntimeError(
                "Google Calendar dependencies are not installed. "
                "Install the project dependencies before using the live calendar integration."
            ) from exc

        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), CALENDAR_SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(self._credentials_path), CALENDAR_SCOPES)
            creds = flow.run_local_server(port=0)
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
        elif hasattr(creds, "to_json") and not self._token_path.exists():
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
        elif self._token_path.exists():
            try:
                json.loads(self._token_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("token.json is not valid JSON") from exc

        return build("calendar", "v3", credentials=creds)


class _BaseStubWorker:
    def __init__(self) -> None:
        self._cancelled: dict[str, bool] = {}

    def cancel_request(self, request_id: str) -> None:
        self._cancelled[request_id] = True

    async def shutdown(self) -> None:
        return None


class DocumentWorker(_BaseStubWorker):
    def create_event_document(self, office_admin_ref: Any, request_id: str, event: CalendarEvent) -> None:
        raise NotImplementedError("DocumentWorker is not part of Phase 1")


class PrinterWorker(_BaseStubWorker):
    def print_document(
        self,
        office_admin_ref: Any,
        request_id: str,
        event_id: str,
        document_path: str,
    ) -> None:
        raise NotImplementedError("PrinterWorker is not part of Phase 1")


class MailWorker(_BaseStubWorker):
    def create_email_draft(self, office_admin_ref: Any, request_id: str, event: CalendarEvent) -> None:
        raise NotImplementedError("MailWorker is not part of Phase 1")
