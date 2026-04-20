const STAGE_LABELS = {
  PENDING: "Pending",
  GETTING_CALENDAR_EVENTS: "Calendar Worker getting calendar events",
  CREATING_EVENT_PDFS: "Document Worker creating PDFs",
  PRINTING_EVENT_PDFS: "Printer Worker printing PDFs",
  CREATING_EMAIL_DRAFTS: "Mail Worker creating email drafts",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
  ERROR: "Error",
};

const TERMINAL_STATUSES = new Set(["COMPLETED", "CANCELLED", "ERROR"]);
const tasks = new Map();
const pollers = new Map();

function toLocalDateString(offsetDays = 0) {
  const value = new Date();
  value.setDate(value.getDate() + offsetDays);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(
    value.getDate(),
  ).padStart(2, "0")}`;
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (response.status === 204) {
    return null;
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "Request failed");
    error.status = response.status;
    error.payload = data.detail || data;
    throw error;
  }
  return data;
}

function setMessage(text, isError = false) {
  const node = document.querySelector("#message");
  node.textContent = text;
  node.dataset.state = isError ? "error" : "ok";
}

function renderTask(task) {
  const container = document.querySelector("#tasks");
  let card = document.querySelector(`[data-request-id="${task.request_id}"]`);

  if (!card) {
    const template = document.querySelector("#task-template");
    card = template.content.firstElementChild.cloneNode(true);
    card.dataset.requestId = task.request_id;
    container.prepend(card);
    card.querySelector(".cancel-button").addEventListener("click", () => cancelTask(task.request_id));
  }

  card.querySelector(".task-id").textContent = task.request_id;
  card.querySelector(".task-date").textContent = task.selected_date;
  card.querySelector(".task-status").textContent = task.status;
  card.querySelector(".task-stage").textContent = STAGE_LABELS[task.stage] || task.stage;
  card.querySelector(".task-progress").textContent = buildProgressLabel(task);

  const cancelButton = card.querySelector(".cancel-button");
  cancelButton.hidden = TERMINAL_STATUSES.has(task.status);

  const errors = card.querySelector(".task-errors");
  errors.innerHTML = "";
  if (task.errors?.length) {
    const errorList = document.createElement("ul");
    for (const errorText of task.errors) {
      const item = document.createElement("li");
      item.textContent = errorText;
      errorList.appendChild(item);
    }
    errors.appendChild(errorList);
  }

  const documentsNode = card.querySelector(".task-documents");
  documentsNode.innerHTML = "";
  if (Array.isArray(task.document_paths) && task.document_paths.length > 0) {
    const heading = document.createElement("p");
    heading.className = "events-heading";
    heading.textContent = "Generated documents";
    documentsNode.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "event-list";
    for (const documentPath of task.document_paths) {
      const item = document.createElement("li");
      const fileName = documentPath.split("/").pop();
      item.innerHTML = `<strong>${fileName}</strong><span>${documentPath}</span>`;
      list.appendChild(item);
    }
    documentsNode.appendChild(list);
  }
}

function buildProgressLabel(task) {
  if (!task.events_retrieved) {
    return "Waiting for calendar results";
  }
  if (task.calendar_event_count === 0) {
    return "No printable events found";
  }
  if (task.stage === "PRINTING_EVENT_PDFS" || task.prints_expected > 0) {
    return `${task.prints_completed} of ${task.prints_expected} PDFs printed`;
  }
  if (task.documents_expected > 0) {
    return `${task.documents_completed} of ${task.documents_expected} PDFs created`;
  }
  return `${task.calendar_event_count} printable calendar event${task.calendar_event_count === 1 ? "" : "s"} found`;
}

async function pollTask(requestId) {
  try {
    const task = await apiFetch(`/api/office/status/${requestId}`);
    tasks.set(requestId, task);
    renderTask(task);
    if (TERMINAL_STATUSES.has(task.status)) {
      stopPolling(requestId);
    }
  } catch (error) {
    stopPolling(requestId);
    setMessage(`Polling failed for ${requestId}: ${error.message}`, true);
  }
}

function startPolling(requestId) {
  stopPolling(requestId);
  pollTask(requestId);
  pollers.set(
    requestId,
    window.setInterval(() => {
      pollTask(requestId);
    }, 2000),
  );
}

function stopPolling(requestId) {
  const handle = pollers.get(requestId);
  if (handle) {
    window.clearInterval(handle);
    pollers.delete(requestId);
  }
}

async function cancelTask(requestId) {
  try {
    const task = await apiFetch(`/api/office/cancel/${requestId}`, { method: "POST" });
    tasks.set(requestId, task);
    renderTask(task);
  } catch (error) {
    setMessage(`Cancel failed for ${requestId}: ${error.message}`, true);
  }
}

async function submitPrintForm(event) {
  event.preventDefault();
  const selectedDate = document.querySelector("#print-date").value;

  try {
    const data = await apiFetch("/api/office/print-calendar-events", {
      method: "POST",
      body: JSON.stringify({ selected_date: selectedDate }),
    });
    setMessage(`Submitted request ${data.request_id}`);
    startPolling(data.request_id);
  } catch (error) {
    if (error.status === 429) {
      setMessage("Server busy, please try again", true);
      return;
    }
    setMessage(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelector("#print-date").value = toLocalDateString();
  document.querySelector("#print-form").addEventListener("submit", submitPrintForm);
});
