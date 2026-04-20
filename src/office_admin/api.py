from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from office_admin.admin import OfficeAdmin, OfficeAdminQueueFullError
from office_admin.workers import CalendarWorker, DocumentWorker, MailWorker, PrinterWorker

ROOT_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))


class PrintCalendarEventsRequest(BaseModel):
    selected_date: date


class SendEmailNotificationsRequest(BaseModel):
    selected_date: date


class SubmitResponse(BaseModel):
    request_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    calendar_worker = CalendarWorker()
    document_worker = DocumentWorker()
    printer_worker = PrinterWorker()
    mail_worker = MailWorker()
    office_admin = OfficeAdmin(calendar_worker, document_worker, printer_worker, mail_worker)

    app.state.calendar_worker = calendar_worker
    app.state.document_worker = document_worker
    app.state.printer_worker = printer_worker
    app.state.mail_worker = mail_worker
    app.state.office_admin = office_admin
    yield
    await office_admin.shutdown()
    await mail_worker.shutdown()
    await printer_worker.shutdown()
    await document_worker.shutdown()
    await calendar_worker.shutdown()


app = FastAPI(title="Office Admin", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/api/office/print-calendar-events", response_model=SubmitResponse, status_code=202)
async def submit_print_calendar_events(payload: PrintCalendarEventsRequest, request: Request) -> SubmitResponse:
    office_admin: OfficeAdmin = request.app.state.office_admin
    try:
        request_id = office_admin.submit_print_calendar_events(payload.selected_date.isoformat())
    except OfficeAdminQueueFullError as exc:
        raise HTTPException(status_code=429, detail="Server is busy. Try again shortly.") from exc
    return SubmitResponse(request_id=request_id)


@app.post("/api/office/send-email-notifications", response_model=SubmitResponse, status_code=202)
async def submit_send_email_notifications(payload: SendEmailNotificationsRequest, request: Request) -> SubmitResponse:
    office_admin: OfficeAdmin = request.app.state.office_admin
    try:
        request_id = office_admin.submit_send_email_notifications(payload.selected_date.isoformat())
    except OfficeAdminQueueFullError as exc:
        raise HTTPException(status_code=429, detail="Server is busy. Try again shortly.") from exc
    return SubmitResponse(request_id=request_id)


@app.get("/api/office/status/{request_id}")
async def get_status(request_id: str, request: Request) -> dict:
    office_admin: OfficeAdmin = request.app.state.office_admin
    status = office_admin.get_status(request_id)
    if status["status"] == "UNKNOWN":
        return JSONResponse(status_code=404, content=status)
    return status


@app.get("/api/office/tasks")
async def get_tasks(request: Request) -> list[dict]:
    office_admin: OfficeAdmin = request.app.state.office_admin
    return office_admin.list_tasks()


@app.post("/api/office/cancel/{request_id}")
async def cancel_request(request_id: str, request: Request) -> dict:
    office_admin: OfficeAdmin = request.app.state.office_admin
    status = office_admin.cancel_request(request_id)
    if status["status"] == "UNKNOWN":
        return JSONResponse(status_code=404, content=status)
    return status
