# Service Request Management API

**Candidate:** Ineedi Venkata Sai Anirudh | **Assignment date:** 31-10-2026

A small REST API for a residential community to record maintenance requests, track status changes and report pending work. Python 3.10+, FastAPI, SQLite (standard-library `sqlite3`, no ORM), Pydantic v2, pytest.

Other documents: [docs/DESIGN.md](docs/DESIGN.md) (design note, incl. how validation is shared) and [docs/EFFORT_LOG.md](docs/EFFORT_LOG.md) (effort log).

## 1. Setup

Requires Python 3.10 or newer. From the project root:

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. Run the API

```
python -m uvicorn app.main:app
```

The API is at <http://127.0.0.1:8000>; interactive docs at <http://127.0.0.1:8000/docs>. Stop with `Ctrl+C`.

## 3. Run the tests

```
python -m pytest -v
```

144 tests. Every test builds the app with its own temporary SQLite file (`tests/conftest.py`), so tests never touch the real database.

## 4. Database initialisation and reset

The database file is `data/requests.db` (override with the `SRM_DB_PATH` environment variable). The folder, file and table are created **automatically** when the server starts or the importer first runs; nothing to run by hand. Data is stored on disk, so it survives a restart.

To reset: stop the server, delete the file, start again (an empty database is created).

```powershell
Remove-Item data\requests.db        # PowerShell
```
```bash
rm data/requests.db                 # bash / zsh
```

## 5. Sample API calls

With the server running (PowerShell; use `curl` equivalents on macOS/Linux):

```powershell
$B = "http://127.0.0.1:8000"

# Create (201)
$body = @{ title = "Water leakage"; description = ""; category = "plumbing"; priority = "high"; due_date = "2026-10-29" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$B/requests" -ContentType "application/json" -Body $body

# List with filters + paging (filters combine with AND)
Invoke-RestMethod "$B/requests?category=plumbing&priority=high&status=open&page=1&page_size=10"

# Get one (404 if missing)
Invoke-RestMethod "$B/requests/1"

# Change status: open -> in_progress -> resolved
Invoke-RestMethod -Method Patch -Uri "$B/requests/1/status" -ContentType "application/json" -Body '{"status":"in_progress"}'

# Rejected change (409): request 1 is now in_progress, so going back to open is not allowed
try { Invoke-RestMethod -Method Patch -Uri "$B/requests/1/status" -ContentType "application/json" -Body '{"status":"open"}' } catch { $_.ErrorDetails.Message }

# Summary report
Invoke-RestMethod "$B/reports/summary?as_of=2026-10-31"
```

```bash
curl -s -X POST http://127.0.0.1:8000/requests -H "Content-Type: application/json" \
  -d '{"title":"Water leakage","description":"","category":"plumbing","priority":"high","due_date":"2026-10-29"}'
curl -s "http://127.0.0.1:8000/requests?category=plumbing&page=1&page_size=10"
curl -s -X PATCH http://127.0.0.1:8000/requests/1/status -H "Content-Type: application/json" -d '{"status":"in_progress"}'
curl -s "http://127.0.0.1:8000/reports/summary?as_of=2026-10-31"
```

Status codes: `201` created, `404` missing record, `409` prohibited transition, `422` validation error. Errors are JSON: `{"error": {"code": "...", "message": "...", "details": [...]}}`.

## 6. Report demo (three records)

Start from an empty database. Create three requests with `description` set to `""`, then change statuses:

| Title | Category | Priority | Due date | Final status |
|---|---|---|---|---|
| Water leakage | plumbing | high | 2026-10-29 | in_progress |
| Gate light failure | electrical | medium | 2026-10-30 | resolved (via in_progress) |
| Camera check | security | low | 2026-11-02 | open |

`GET /reports/summary?as_of=2026-10-31` returns `open=1, in_progress=1, resolved=1`, `low=1, medium=1, high=1`, `overdue_count=1`. A due date equal to `as_of` is not overdue. This scenario is automated in `test_demo_scenario_summary` (`tests/test_reports.py`).

## 7. CSV importer

```
python -m app.importer <csv_path> [--db PATH] [--errors PATH]
```

- Header must be exactly `title,description,category,priority,due_date`; a wrong or missing header rejects the whole file before anything is imported (exit code 2).
- Each row is validated with the same `RequestCreate` model the API uses. Valid rows are imported even when others are invalid; duplicates create separate requests.
- Prints `Imported` and `Rejected` counts and writes an error CSV (`row_number,reason`, header = row 1).

Sample (empty database): `sample_data/sample_requests.csv` has 3 valid rows and 2 invalid rows (row 4 blank title, row 6 invalid category).

```
python -m app.importer sample_data/sample_requests.csv --errors import_errors.csv
```

```
Imported: 3
Rejected: 2
```

Error output (committed as `sample_data/import_errors.csv`):

```csv
row_number,reason
4,title: title must not be blank
6,"category: Input should be 'electrical', 'plumbing' or 'security'"
```

Test this on an empty database, separately from the report demo (reset in between).

## 8. Assumptions

- `due_date` and `as_of` must be strict `YYYY-MM-DD` real dates; past due dates are allowed.
- Enums are lower case; `title` is trimmed before the 5–100 check; `description` is optional (stored as `""`).
- Unknown fields, and client-supplied `id`, `status`, `created_at`, `resolved_at`, are rejected with 422.
- Validation errors use 422; timestamps are UTC (`YYYY-MM-DDTHH:MM:SSZ`).
- Repeating the current status returns 200 and changes nothing. `resolved_at` is set only on resolution.
- Overdue = `due_date < as_of` and status not `resolved`; `as_of` defaults to the current UTC date.
- List order is by `id` ascending.

## 9. Incomplete items

None known. Out of scope per the brief: frontend, login, deployment, attachments, notifications. Not implemented: editing or deleting requests other than changing status.

## 10. Tools disclosure

Built with assistance from Claude Code (Anthropic's Claude) for scaffolding, code, tests and documentation. Libraries: FastAPI, Pydantic, Uvicorn, httpx, pytest. No external code snippets were copied.
