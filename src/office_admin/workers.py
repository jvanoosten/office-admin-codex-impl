from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol, TypedDict

from office_admin.models import CalendarEvent, CalendarWorkItem, DocumentWorkItem, MailWorkItem, PrinterWorkItem

LOGGER = logging.getLogger(__name__)
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
CUSTOMER_EMAIL = "examplecompany@gmail.com"
CUSTOMER_TITLE = "Created by: Example Company"
COLOR_MAP = {
    "1": (0.475, 0.525, 0.796),
    "2": (0.153, 0.941, 0.729),
    "3": (0.557, 0.141, 0.667),
    "4": (0.902, 0.486, 0.451),
    "5": (0.965, 0.749, 0.149),
    "6": (0.957, 0.318, 0.118),
    "7": (0.012, 0.608, 0.898),
    "8": (0.380, 0.380, 0.380),
    "9": (0.247, 0.318, 0.710),
    "10": (0.043, 0.502, 0.263),
    "11": (0.835, 0.000, 0.000),
}


class CalendarEventsService(Protocol):
    def list(self, **kwargs: Any) -> Any: ...


class CalendarService(Protocol):
    def events(self) -> CalendarEventsService: ...


class GmailDraftsService(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class GmailUsersService(Protocol):
    def drafts(self) -> GmailDraftsService: ...


class GmailService(Protocol):
    def users(self) -> GmailUsersService: ...


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
    def __init__(
        self,
        output_dir: str = "reports",
        prune_age_days: int = 7,
        pdf_generator: Callable[[CalendarEvent, Path], None] | None = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._prune_age_days = prune_age_days
        self._pdf_generator = pdf_generator or self._generate_pdf
        self._queue: asyncio.Queue[DocumentWorkItem | None] = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker_loop())

    def create_event_document(self, office_admin_ref: Any, request_id: str, event: CalendarEvent) -> None:
        event_id = event.get("id")
        if not event_id:
            raise ValueError("DocumentWorker requires an event id")
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: DocumentWorkItem) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        event = item["event"]
        event_id = str(event["id"])

        if self._cancelled.get(request_id):
            await office_admin_ref.document_failed(request_id, event_id, "Cancelled")
            return

        output_path = self._build_output_path(request_id, event)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._generate_document, event, output_path)
        except Exception as exc:
            await office_admin_ref.document_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.document_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.document_complete(request_id, event_id, str(output_path))

    def _generate_document(self, event: CalendarEvent, output_path: Path) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._prune_stale_pdfs()
        self._pdf_generator(event, output_path)

    def _build_output_path(self, request_id: str, event: CalendarEvent) -> Path:
        event_id = self._sanitize_fragment(str(event["id"]), 20)
        summary = self._sanitize_fragment(event.get("summary") or "event", 40)
        return self._output_dir / f"{request_id[:8]}_{event_id}_{summary}.pdf"

    @staticmethod
    def _sanitize_fragment(value: str, max_length: int) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
        normalized = re.sub(r"-{2,}", "-", normalized)
        return (normalized or "event")[:max_length]

    def _prune_stale_pdfs(self) -> None:
        if not self._output_dir.exists():
            return

        cutoff = dt.datetime.now().timestamp() - (self._prune_age_days * 24 * 60 * 60)
        for entry in self._output_dir.iterdir():
            if entry.is_dir() or entry.suffix.lower() != ".pdf":
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                LOGGER.warning("Failed to remove stale PDF", extra={"path": str(entry)})

    @staticmethod
    def _generate_pdf(event: CalendarEvent, output_path: Path) -> None:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        page_width, page_height = letter
        pdf = canvas.Canvas(str(output_path), pagesize=letter)
        left = 54
        right = page_width - 54
        header_height = 36
        color = COLOR_MAP.get(event.get("colorId"), (0.530, 0.810, 0.980))

        pdf.setFillColorRGB(*color)
        pdf.rect(0, page_height - header_height, 72, header_height, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica", 10)
        pdf.drawRightString(right, page_height - 23, CUSTOMER_EMAIL)

        title = event.get("summary") or "(No Title)"
        pdf.setFont("Helvetica-Bold", 24)
        y = page_height - 72
        for line in DocumentWorker._wrap_text(title, 34):
            pdf.drawString(left, y, line)
            y -= 28

        pdf.setFillColorRGB(0.45, 0.45, 0.45)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(left, y, CUSTOMER_TITLE)
        y -= 36
        pdf.setFillColorRGB(0, 0, 0)

        sections = [
            ("TIME", DocumentWorker._format_time_range(event)),
            ("DATE", DocumentWorker._format_date(event.get("start", ""))),
            ("WHERE", event.get("location") or "No location"),
            ("DESCRIPTION", event.get("description") or "No description"),
        ]
        for label, value in sections:
            pdf.setFillColorRGB(0.45, 0.45, 0.45)
            pdf.setFont("Helvetica", 8)
            pdf.drawString(left, y, label)
            y -= 16
            pdf.setFillColorRGB(0, 0, 0)
            pdf.setFont("Helvetica-Bold" if label in {"TIME", "DATE", "WHERE"} else "Helvetica", 18 if label != "DESCRIPTION" else 12)
            for line in DocumentWorker._wrap_text(value, 55 if label != "DESCRIPTION" else 80):
                pdf.drawString(left, y, line)
                y -= 20 if label != "DESCRIPTION" else 14
            y -= 18

        pdf.showPage()
        pdf.save()

    @staticmethod
    def _wrap_text(value: str, width: int) -> list[str]:
        words = value.split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    @staticmethod
    def _format_date(value: str) -> str:
        if not value:
            return "Unknown date"
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return DocumentWorker._format_display_date(parsed.astimezone().date())
        except ValueError:
            try:
                return DocumentWorker._format_display_date(dt.date.fromisoformat(value))
            except ValueError:
                return value

    @staticmethod
    def _format_display_date(value: dt.date) -> str:
        return value.strftime("%a %b %d, %Y").replace(" 0", " ")

    @staticmethod
    def _format_time_range(event: CalendarEvent) -> str:
        start = event.get("start", "")
        end = event.get("end", "")
        if "T" not in start or "T" not in end:
            return "All Day"
        try:
            start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
            end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone()
        except ValueError:
            return f"{start} - {end}"

        return f"{DocumentWorker._format_time(start_dt)} - {DocumentWorker._format_time(end_dt)}"

    @staticmethod
    def _format_time(value: dt.datetime) -> str:
        formatted = value.strftime("%I:%M%p").lower()
        if formatted.startswith("0"):
            formatted = formatted[1:]
        return formatted.replace(":00", "")


class PrinterWorker(_BaseStubWorker):
    def __init__(self, print_adapter: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._print_adapter = print_adapter or self._default_print_adapter
        self._queue: asyncio.Queue[PrinterWorkItem | None] = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker_loop())

    def print_document(
        self,
        office_admin_ref: Any,
        request_id: str,
        event_id: str,
        document_path: str,
    ) -> None:
        if not document_path:
            raise ValueError("PrinterWorker requires a non-empty document path")
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event_id": event_id,
                "document_path": document_path,
            }
        )

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: PrinterWorkItem) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        event_id = item["event_id"]
        document_path = item["document_path"]

        if self._cancelled.get(request_id):
            await office_admin_ref.print_failed(request_id, event_id, "Cancelled")
            return

        if not Path(document_path).exists():
            await office_admin_ref.print_failed(request_id, event_id, f"File not found: {document_path}")
            return

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._print_adapter, document_path)
        except Exception as exc:
            await office_admin_ref.print_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.print_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.print_complete(request_id, event_id, document_path)

    @staticmethod
    def _default_print_adapter(document_path: str) -> None:
        if os.name == "nt":
            raise RuntimeError("Default printer adapter is not implemented for Windows")
        subprocess.run(["lp", document_path], check=True)


class MailWorker(_BaseStubWorker):
    _EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")

    def __init__(
        self,
        service_factory: Callable[[], GmailService] | None = None,
        credentials_path: str = "gmail_credentials.json",
        token_path: str = "gmail_token.json",
    ) -> None:
        super().__init__()
        self._service_factory = service_factory or self._build_service
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._service: GmailService | None = None
        self._queue: asyncio.Queue[MailWorkItem | None] = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker_loop())

    def create_email_draft(self, office_admin_ref: Any, request_id: str, event: CalendarEvent) -> None:
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: MailWorkItem) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        event = item["event"]
        event_id = str(event["id"])

        if self._cancelled.get(request_id):
            await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
            return

        recipients = self._extract_recipients(event.get("description"))
        if not recipients:
            if self._cancelled.get(request_id):
                await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
                return
            await office_admin_ref.email_draft_skipped(request_id, event_id)
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
            return

        try:
            service = await self._get_service()
            encoded_message = await self._create_encoded_message(event, recipients)
            draft_id = await self._create_draft(service, encoded_message)
        except Exception as exc:
            await office_admin_ref.email_draft_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.email_draft_complete(request_id, event_id, draft_id)

    async def _get_service(self) -> GmailService:
        if self._service is None:
            loop = asyncio.get_running_loop()
            self._service = await loop.run_in_executor(None, self._service_factory)
        return self._service

    async def _create_encoded_message(self, event: CalendarEvent, recipients: list[str]) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._compose_draft_body, event, recipients)

    async def _create_draft(self, service: GmailService, encoded_message: str) -> str:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: service.users().drafts().create(userId="me", body={"message": {"raw": encoded_message}}).execute(),
        )
        return str(response["id"])

    @classmethod
    def _extract_recipients(cls, description: str | None) -> list[str]:
        matches = cls._EMAIL_RE.findall(description or "")
        seen: set[str] = set()
        results: list[str] = []
        for addr in matches:
            if addr not in seen:
                seen.add(addr)
                results.append(addr)
        return results

    def _compose_draft_body(self, event: CalendarEvent, recipients: list[str], template: str | None = None) -> str:
        template_text = template if template is not None else self._load_template()
        lines = template_text.splitlines()
        subject = (lines[0] if lines else "Example Company: Upcoming Service").strip()
        body_template = "\n".join(lines[1:]).lstrip("\n")

        body = body_template.format(
            date=self._format_event_date(event),
            time=self._format_event_time(event),
            location=event.get("location") or "",
        )

        message = EmailMessage()
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    @staticmethod
    def _format_event_date(event: CalendarEvent) -> str:
        start = event.get("start", "")
        if not start:
            return "Unknown date"
        if "T" in start:
            parsed = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
            return parsed.strftime("%A, %B %d, %Y").replace(" 0", " ")
        return dt.date.fromisoformat(start).strftime("%A, %B %d, %Y").replace(" 0", " ")

    @staticmethod
    def _format_event_time(event: CalendarEvent) -> str:
        start = event.get("start", "")
        end = event.get("end", "")
        if not start or not end:
            return "Unknown time"
        if "T" not in start or "T" not in end:
            return "(all day)"
        start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
        end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone()
        return f"{start_dt.strftime('%I:%M %p').lstrip('0')} – {end_dt.strftime('%I:%M %p').lstrip('0')}"

    @staticmethod
    def _load_template() -> str:
        root_dir = Path(__file__).resolve().parents[2]
        return (root_dir / "templates" / "email_notification_template").read_text(encoding="utf-8")

    def _build_service(self) -> GmailService:
        creds = None
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Gmail dependencies are not installed. Install the project dependencies before using the mail integration."
            ) from exc

        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), GMAIL_SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(self._credentials_path), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
        elif hasattr(creds, "to_json") and not self._token_path.exists():
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
        elif self._token_path.exists():
            try:
                json.loads(self._token_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("gmail_token.json is not valid JSON") from exc

        return build("gmail", "v1", credentials=creds)
