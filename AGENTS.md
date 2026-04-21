# Repository Guidelines

## Project Structure & Module Organization
- Application code lives in `src/office_admin/`.
  - `api.py` wires the FastAPI app and routes.
  - `admin.py` contains `OfficeAdmin`, the async workflow orchestrator.
  - `workers.py` contains `CalendarWorker`, `DocumentWorker`, `PrinterWorker`, and `MailWorker`.
  - `models.py` defines task constants and message/status payload shapes.
- UI assets live in `templates/` and `static/`.
- Tests live in `tests/` and are organized by component, for example `tests/test_office_admin.py` and `tests/test_mail_worker.py`.
- Specs and roadmap docs live in `docs/` and `specs/`. Read these before changing workflow behavior.

## Build, Test, and Development Commands
- `uv run main.py`: run the FastAPI app locally on `127.0.0.1:8000`.
- `UV_CACHE_DIR=$(pwd)/.uv-cache uv run pytest -q`: run the full test suite with a repo-local uv cache.
- `python3 -m compileall src tests main.py`: quick syntax verification.
- `uv sync --extra dev`: install runtime and development dependencies from `pyproject.toml`.

## Coding Style & Naming Conventions
- Use Python 3.11+ with 4-space indentation and type hints on public methods.
- Keep orchestration in `OfficeAdmin`; external system logic belongs in worker classes.
- Use `snake_case` for functions and variables, `PascalCase` for classes, and uppercase constants such as `PRINT_CALENDAR_EVENTS`.
- Follow the existing async queue pattern for new workers: enqueue, process in a background task, callback exactly once.

## Testing Guidelines
- Tests use `pytest` and `pytest-asyncio`.
- Name test files `tests/test_<component>.py` and test functions `test_<behavior>()`.
- Prefer fake workers and injected adapters/services over live Google APIs, printers, or OAuth.
- For workflow changes, add orchestration tests in `tests/test_office_admin.py` and component tests alongside the worker under test.

## Commit & Pull Request Guidelines
- Keep commit messages short and imperative, matching existing history, for example: `implement phase 3 printer integration`.
- Use feature branches named `features/<phase-or-scope>`.
- PRs should include:
  - a concise summary of what changed
  - why it changed
  - validation commands and results
  - draft status by default unless the branch is ready for review

## Security & Configuration Tips
- Do not commit real Google credential or token files.
- Gmail and Calendar OAuth use separate credential/token paths; keep local secrets untracked.
- Prefer injected test doubles for external integrations to avoid hitting live services during development.
