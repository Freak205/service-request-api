# Service Request Management API

A small REST API for a residential community to record maintenance requests (plumbing, electrical, security), move them through a fixed status workflow, bulk-load them from CSV and report on pending work.

- **Python 3.10+**, **FastAPI**, **SQLite** (standard-library `sqlite3`, no ORM)
- **Pydantic v2** models are the single source of truth for the creation rules, shared by the HTTP API and the CSV importer
- **pytest** suite (144 tests) where every test runs against its own temporary database

**Candidate:** Ineedi Venkata Sai Anirudh  |  **Assignment date:** 31-10-2026

Related documents: [docs/DESIGN.md](docs/DESIGN.md) (design note) and [docs/EFFORT_LOG.md](docs/EFFORT_LOG.md) (effort log).

---

## Table of contents

1. [Project structure](#project-structure)
2. [Prerequisites and setup](#prerequisites-and-setup)
3. [Running the API](#running-the-api)
4. [Database location, initialisation and reset](#database-location-initialisation-and-reset)
5. [Running the tests](#running-the-tests)
6. [Sample API calls](#sample-api-calls)
7. [API reference](#api-reference)
8. [Business rules](#business-rules)
9. [CSV importer](#csv-importer)
10. [Reproducible report demo](#reproducible-report-demo)
11. [Assumptions and decisions](#assumptions-and-decisions)
12. [Incomplete items, known limitations and future improvements](#incomplete-items-known-limitations-and-future-improvements)
13. [AI and tool disclosure](#ai-and-tool-disclosure)

---

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py        # database path (SRM_DB_PATH override)
│   ├── validation.py    # RequestCreate / StatusUpdate models, shared error formatting
│   ├── db.py            # SQLite schema + RequestRepository (all SQL lives here)
│   ├── main.py          # FastAPI app factory create_app(), routes, error handlers
│   └── importer.py      # CSV importer CLI (python -m app.importer)
├── sample_data/
│   ├── sample_requests.csv   # 3 valid + 2 invalid rows
│   └── import_errors.csv     # error report the sample file produces
├── tests/
│   ├── conftest.py      # isolated temp-DB client fixtures
│   ├── test_api.py          # create / validate / get / filter / paginate / status transitions
│   ├── test_reports.py      # GET /reports/summary, overdue boundary, as_of
│   ├── test_importer.py     # CSV importer
│   └── test_persistence.py  # data survives a restart; init is idempotent
├── docs/
│   ├── DESIGN.md
│   └── EFFORT_LOG.md
├── pytest.ini
├── requirements.txt
└── README.md
```

`data/requests.db` is created at runtime and is git-ignored.

---

## Prerequisites and setup

You need **Python 3.10 or newer** (the code uses `X | Y` type unions) and `pip`. It was developed and tested on Windows 11; macOS and Linux work the same way.

### Windows (PowerShell)

```powershell
cd "path\to\project"            # the folder containing requirements.txt
python -m venv .venv
.\.venv\Scripts\Activate.ps1    # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install -r requirements.txt
```

### macOS / Linux (bash or zsh)

```bash
cd path/to/project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` pins only lower bounds: `fastapi`, `uvicorn`, `pydantic>=2.5`, `httpx` (needed by FastAPI's `TestClient`) and `pytest`.

---

## Running the API

From the project root with the virtual environment active (same command on every OS):

```
python -m uvicorn app.main:app --reload
```

- API root: <http://127.0.0.1:8000>
- Interactive Swagger UI: <http://127.0.0.1:8000/docs> (ReDoc at `/redoc`)
- Stop with `Ctrl+C`. Drop `--reload` if you do not want auto-reload; add `--port 8010` to use another port.

---

## Database location, initialisation and reset

| Item | Value |
|---|---|
| Default file | `data/requests.db` under the project root |
| Override | environment variable `SRM_DB_PATH` (absolute or relative path to the `.db` file) |
| Initialisation | **Automatic.** The `data/` folder, the file and the `requests` table (with indexes) are created when the server starts, or on first use by the importer. Nothing to run by hand and it is safe to run repeatedly (`CREATE TABLE IF NOT EXISTS`). |

Use a different database for one session:

```powershell
# PowerShell
$env:SRM_DB_PATH = "$env:TEMP\srm-scratch.db"
python -m uvicorn app.main:app --reload
```

```bash
# bash / zsh
SRM_DB_PATH=/tmp/srm-scratch.db python -m uvicorn app.main:app --reload
```

### Resetting the database

1. Stop the server (`Ctrl+C`).
2. Delete the database file.
3. Start the server again - an empty database is created.

```powershell
Remove-Item data\requests.db        # PowerShell
```

```bash
rm data/requests.db                 # bash / zsh
```

(If you used `SRM_DB_PATH`, delete that file instead. You may also see `-journal` files next to it; they can be deleted too once the server is stopped.)

---

## Running the tests

```
python -m pytest -v
```

The suite has **144 tests** (`test_api.py` 92, `test_importer.py` 26, `test_reports.py` 19, `test_persistence.py` 7) and takes a few seconds. At the time of writing all pass (`144 passed`; one third-party `httpx`/Starlette deprecation warning is printed).

Tests never touch `data/requests.db`: the `client` fixture in `tests/conftest.py` builds the app with `create_app(tmp_path / "test_requests.db")`, so each test gets a brand-new throw-away SQLite file. Run a subset with, for example, `python -m pytest -v tests/test_reports.py` or `python -m pytest -v -k overdue`.

---

## Sample API calls

The examples below assume a freshly reset database and a server on `http://127.0.0.1:8000`. The response bodies are real output captured from a running server (only the `created_at` / `resolved_at` values will differ on your machine; they are the server's UTC time).

> **PowerShell note:** use `Invoke-RestMethod` (shown first). In Windows PowerShell 5.1 `curl` is an alias for `Invoke-WebRequest`, and passing JSON to `curl.exe` needs awkward quote escaping, so the `curl` commands are given for bash/zsh (macOS, Linux, Git Bash). On a non-2xx status `Invoke-RestMethod` throws; read the JSON error body from `$_.ErrorDetails.Message` as shown.

### 1. Create a request - `POST /requests` (201)

PowerShell:

```powershell
$B = "http://127.0.0.1:8000"
$body = @{ title = "Water leakage"; description = "Pipe leaking in basement"; category = "plumbing"; priority = "high"; due_date = "2026-10-29" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$B/requests" -ContentType "application/json" -Body $body
```

curl:

```bash
B=http://127.0.0.1:8000
curl -s -X POST $B/requests -H "Content-Type: application/json" \
  -d '{"title":"Water leakage","description":"Pipe leaking in basement","category":"plumbing","priority":"high","due_date":"2026-10-29"}'
```

Response `201 Created`:

```json
{"id":1,"title":"Water leakage","description":"Pipe leaking in basement","category":"plumbing","priority":"high","status":"open","created_at":"2026-10-31T15:59:41Z","due_date":"2026-10-29","resolved_at":null}
```

Create a second record (id 2) so later steps have something to work with:

```powershell
$body = @{ title = "Gate light failure"; description = "Main gate light is out"; category = "electrical"; priority = "medium"; due_date = "2026-10-30" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$B/requests" -ContentType "application/json" -Body $body
```

```bash
curl -s -X POST $B/requests -H "Content-Type: application/json" \
  -d '{"title":"Gate light failure","description":"Main gate light is out","category":"electrical","priority":"medium","due_date":"2026-10-30"}'
```

### 2. List with filters and pagination - `GET /requests` (200)

Filters combine with AND; `page` starts at 1, `page_size` defaults to 10 (max 100).

```powershell
Invoke-RestMethod "$B/requests?category=plumbing&priority=high&status=open&page=1&page_size=10"
```

```bash
curl -s "$B/requests?category=plumbing&priority=high&status=open&page=1&page_size=10"
```

```json
{"items":[{"id":1,"title":"Water leakage","description":"Pipe leaking in basement","category":"plumbing","priority":"high","status":"open","created_at":"2026-10-31T15:59:41Z","due_date":"2026-10-29","resolved_at":null}],"total":1,"page":1,"page_size":10}
```

`total` is the number of matching records across all pages. For example `?page_size=1` returns one item with `"total":2`.

### 3. Get one request - `GET /requests/{id}` (200)

```powershell
Invoke-RestMethod "$B/requests/1"
```

```bash
curl -s $B/requests/1
```

Returns the record object shown in step 1.

### 4. Change status - `PATCH /requests/{id}/status` (200)

```powershell
Invoke-RestMethod -Method Patch -Uri "$B/requests/1/status" -ContentType "application/json" -Body '{"status":"in_progress"}'
```

```bash
curl -s -X PATCH $B/requests/1/status -H "Content-Type: application/json" -d '{"status":"in_progress"}'
```

```json
{"id":1,"title":"Water leakage","description":"Pipe leaking in basement","category":"plumbing","priority":"high","status":"in_progress","created_at":"2026-10-31T15:59:41Z","due_date":"2026-10-29","resolved_at":null}
```

Sending the **same** status again (`in_progress` -> `in_progress`) is accepted: it returns 200 with the record unchanged. Then move it on:

```powershell
Invoke-RestMethod -Method Patch -Uri "$B/requests/1/status" -ContentType "application/json" -Body '{"status":"resolved"}'
```

```bash
curl -s -X PATCH $B/requests/1/status -H "Content-Type: application/json" -d '{"status":"resolved"}'
```

```json
{"id":1,"title":"Water leakage","description":"Pipe leaking in basement","category":"plumbing","priority":"high","status":"resolved","created_at":"2026-10-31T15:59:41Z","due_date":"2026-10-29","resolved_at":"2026-10-31T15:59:41Z"}
```

### 5. A rejected status change (409)

Request 2 is still `open`; jumping straight to `resolved` is not allowed.

PowerShell:

```powershell
try {
  Invoke-RestMethod -Method Patch -Uri "$B/requests/2/status" -ContentType "application/json" -Body '{"status":"resolved"}'
} catch {
  "HTTP $([int]$_.Exception.Response.StatusCode)"
  $_.ErrorDetails.Message
}
```

curl:

```bash
curl -s -w "\nHTTP %{http_code}\n" -X PATCH $B/requests/2/status -H "Content-Type: application/json" -d '{"status":"resolved"}'
```

```text
HTTP 409
{"error":{"code":"invalid_transition","message":"Cannot change status from 'open' to 'resolved'. Allowed transitions: open -> in_progress -> resolved."}}
```

The record is left unchanged.

### 6. A validation error (422)

Title too short, unknown category and an impossible date, all reported together:

```powershell
try {
  Invoke-RestMethod -Method Post -Uri "$B/requests" -ContentType "application/json" `
    -Body '{"title":"Fix","category":"heating","priority":"high","due_date":"2026-02-30"}'
} catch {
  "HTTP $([int]$_.Exception.Response.StatusCode)"
  $_.ErrorDetails.Message
}
```

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST $B/requests -H "Content-Type: application/json" \
  -d '{"title":"Fix","category":"heating","priority":"high","due_date":"2026-02-30"}'
```

```text
HTTP 422
{"error":{"code":"validation_error","message":"title: String should have at least 5 characters; category: Input should be 'electrical', 'plumbing' or 'security'; due_date: must be a real calendar date in YYYY-MM-DD format","details":[{"field":"title","message":"String should have at least 5 characters"},{"field":"category","message":"Input should be 'electrical', 'plumbing' or 'security'"},{"field":"due_date","message":"must be a real calendar date in YYYY-MM-DD format"}]}}
```

### 7. Not found (404)

```powershell
try { Invoke-RestMethod "$B/requests/999" } catch { "HTTP $([int]$_.Exception.Response.StatusCode)"; $_.ErrorDetails.Message }
```

```bash
curl -s -w "\nHTTP %{http_code}\n" $B/requests/999
```

```text
HTTP 404
{"error":{"code":"not_found","message":"Request 999 does not exist."}}
```

### 8. Summary report - `GET /reports/summary?as_of=2026-10-31` (200)

```powershell
Invoke-RestMethod "$B/reports/summary?as_of=2026-10-31"
```

```bash
curl -s "$B/reports/summary?as_of=2026-10-31"
```

With the two records above (request 1 resolved, request 2 still open and due 2026-10-30):

```json
{"as_of":"2026-10-31","total":2,"status_counts":{"open":1,"in_progress":0,"resolved":1},"priority_counts":{"low":0,"medium":1,"high":1},"overdue_count":1}
```

An invalid `as_of` (e.g. `2026-13-01`) returns 422 with `details[0].field == "as_of"`.

---

## API reference

All request and response bodies are JSON. Interactive documentation is generated at `/docs`.

### Endpoints

| Method and path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /requests` | Create a request (always starts `open`) | `201` + request object | `422` |
| `GET /requests` | List requests, filtered and paginated | `200` + page object | `422` (bad enum / paging value) |
| `GET /requests/{id}` | Fetch one request | `200` + request object | `404`, `422` (non-integer id) |
| `PATCH /requests/{id}/status` | Change status | `200` + updated request | `404`, `409`, `422` |
| `GET /reports/summary` | Counts by status/priority and overdue count | `200` + summary object | `422` (bad `as_of`) |

### Create body (`POST /requests`)

| Field | Type | Required | Rules |
|---|---|---|---|
| `title` | string | yes | surrounding whitespace is trimmed, then 5-100 characters; not blank |
| `description` | string | no | up to 1000 characters; omitted or `null` is stored as `""` |
| `category` | string | yes | `electrical`, `plumbing` or `security` (lower case) |
| `priority` | string | yes | `low`, `medium` or `high` (lower case) |
| `due_date` | string | yes | strictly `YYYY-MM-DD`, must be a real calendar date; past dates are allowed |

Any other key (`id`, `status`, `created_at`, `resolved_at`, anything unknown) is rejected with 422. Types are strict: a number for `title`, for example, is an error, not coerced to a string.

### Request object (responses)

| Field | Type | Notes |
|---|---|---|
| `id` | integer | assigned by the database |
| `title` | string | trimmed |
| `description` | string | `""` when none was given |
| `category` | string | `electrical` / `plumbing` / `security` |
| `priority` | string | `low` / `medium` / `high` |
| `status` | string | `open` / `in_progress` / `resolved` |
| `created_at` | string | UTC, `YYYY-MM-DDTHH:MM:SSZ`, set by the server |
| `due_date` | string | `YYYY-MM-DD` |
| `resolved_at` | string or null | UTC timestamp set when the request becomes `resolved`, otherwise `null` |

### List query parameters (`GET /requests`)

| Parameter | Default | Rules |
|---|---|---|
| `category` | none | one of the category values |
| `priority` | none | one of the priority values |
| `status` | none | one of the status values |
| `page` | `1` | integer >= 1 |
| `page_size` | `10` | integer 1-100 |

Results are ordered by `id` ascending. Response: `{"items": [request, ...], "total": <matches across all pages>, "page": <n>, "page_size": <n>}`. A page beyond the end returns an empty `items` list.

### Status change body (`PATCH /requests/{id}/status`)

```json
{"status": "in_progress"}
```

Exactly one key, `status`, with value `open`, `in_progress` or `resolved`. Extra keys are rejected with 422.

### Summary response (`GET /reports/summary?as_of=YYYY-MM-DD`)

| Key | Type | Meaning |
|---|---|---|
| `as_of` | string | the date used (the parameter, or today's UTC date when omitted) |
| `total` | integer | total number of requests |
| `status_counts` | object | `open`, `in_progress`, `resolved` - always all three keys, zero-filled |
| `priority_counts` | object | `low`, `medium`, `high` - always all three keys, zero-filled |
| `overdue_count` | integer | requests with `due_date < as_of` and `status != resolved` |

### Status codes

| Code | When |
|---|---|
| `200` | success (including a repeated, unchanged status) |
| `201` | request created |
| `404` | unknown request id (or unknown route) |
| `409` | status transition not allowed |
| `422` | validation failure: body, query parameter, path parameter or malformed JSON |

### Error shape

Every error uses the same envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "due_date: must be an ISO date in YYYY-MM-DD format",
    "details": [{"field": "due_date", "message": "must be an ISO date in YYYY-MM-DD format"}]
  }
}
```

| `code` | HTTP status | Notes |
|---|---|---|
| `validation_error` | 422 | `details` lists one `{field, message}` per problem; `message` is those joined as `field: message; ...`. Malformed JSON gives `field: "body"`. |
| `not_found` | 404 | no `details` key |
| `invalid_transition` | 409 | no `details` key; message names the current and requested status |
| `method_not_allowed` / `http_error` | 405 / other | generic framework errors, no `details` key |

Note: `details` is only present when there is something to list (validation errors). 404 and 409 responses contain just `code` and `message`.

---

## Business rules

- A new request is always `open`; clients cannot choose `status`, `id`, `created_at` or `resolved_at`.
- The only allowed transitions are **`open` -> `in_progress` -> `resolved`**. Skipping (`open` -> `resolved`), going backwards or reopening returns **409**.
- Repeating the **current** status is allowed: 200 and nothing changes (including `resolved_at`).
- `resolved_at` is set (UTC) when a request becomes `resolved`; it stays `null` before that.
- **Overdue** means `due_date` is strictly earlier than `as_of` **and** the request is not `resolved`. A request due exactly on `as_of` is **not** overdue.
- Timestamps are UTC with second precision, formatted `YYYY-MM-DDTHH:MM:SSZ`.
- The importer applies exactly the same creation rules as `POST /requests`.

---

## CSV importer

```
python -m app.importer <csv_path> [--db PATH] [--errors PATH]
```

| Option | Default | Meaning |
|---|---|---|
| `csv_path` | required | the CSV file to import |
| `--db` | `SRM_DB_PATH`, else `data/requests.db` | database to import into (created if missing) |
| `--errors` | `import_errors.csv` in the current directory | where the rejected-rows report is written |

**CSV format.** The first line must be exactly `title,description,category,priority,due_date` (this order, lower case). Each later line is validated with the same `RequestCreate` model the API uses. The header is **row 1**, so the first data line is row 2 and reported row numbers match what you see in a spreadsheet or editor.

**Behaviour.**

- Valid rows are inserted (as `open` requests) in one transaction; invalid rows never stop the valid ones from importing.
- Output: `Imported: N`, `Rejected: M` and, if M > 0, `Errors written to: <path>`.
- The errors file always contains the header `row_number,reason`, then one line per rejected row with the source row number and the reason (the same text the API puts in its 422 `message`).
- A wrong, missing-column, wrong-order or wrong-case header, an empty file, a missing file, or an unreadable file rejects the **whole** file *before anything is imported*. The tool prints `Import failed: ...` to stderr, writes the reason as row 1 in the errors file and exits with code **2**. Otherwise the exit code is 0, even when some rows were rejected.
- Wrong column count in a row is a rejected row (`expected 5 columns, found N`). Completely blank lines are skipped (not counted as rejects, but they still count for row numbering). A UTF-8 BOM at the start of the file is tolerated.
- The importer does not de-duplicate: importing the same file twice creates the records twice.

### Sample file

`sample_data/sample_requests.csv`:

```csv
title,description,category,priority,due_date
Replace hallway light fixture,Flickering LED fixture outside unit 4B,electrical,medium,2026-11-15
Fix broken front door lock,Deadbolt jams and the key will not turn,security,high,2026-11-05
,Kitchen tap drips constantly,plumbing,low,2026-11-20
Unblock bathroom sink drain,Water drains very slowly in the main bathroom,plumbing,high,2026-11-30
Repair garage opener remote,Remote stopped responding after power cut,carpentry,medium,2026-12-01
```

Rows 2, 3 and 5 are valid. Row 4 has a blank title and row 6 has the unsupported category `carpentry`. On an **empty database** the result is exactly 3 imported and 2 rejected.

Run it (reset the database first if it already holds data; use `--errors` to keep the report out of the project root):

```powershell
# PowerShell
python -m app.importer sample_data\sample_requests.csv --errors "$env:TEMP\import_errors.csv"
```

```bash
# bash / zsh
python -m app.importer sample_data/sample_requests.csv --errors /tmp/import_errors.csv
```

Output (verified):

```text
Imported: 3
Rejected: 2
Errors written to: <the path you passed>
```

The error report is identical to the committed `sample_data/import_errors.csv`:

```csv
row_number,reason
4,title: title must not be blank
6,"category: Input should be 'electrical', 'plumbing' or 'security'"
```

Whole-file rejection (verified), using a file whose first line is `name,desc`:

```text
Import failed: invalid header: expected 'title,description,category,priority,due_date' but found 'name,desc'
Nothing was imported. Details written to: <errors path>
```

(exit code 2; the errors file contains `1,"invalid header: ..."`.)

To check imported data afterwards, start the API on the same database and call `GET /requests`.

---

## Reproducible report demo

Scenario (start from an empty database; keep it separate from the CSV import check, which also expects an empty database):

| Title | Category | Priority | Due | Status changes |
|---|---|---|---|---|
| Water leakage | plumbing | high | 2026-10-29 | `open` -> `in_progress` |
| Gate light failure | electrical | medium | 2026-10-30 | `open` -> `in_progress` -> `resolved` |
| Camera check | security | low | 2026-11-02 | stays `open` |

Create each with `POST /requests` (`description` set to `""`), apply the status changes with `PATCH /requests/{id}/status`, then call `GET /reports/summary?as_of=2026-10-31`. Expected: `open=1, in_progress=1, resolved=1`; `low=1, medium=1, high=1`; `overdue_count=1` (Water leakage is overdue; Gate light failure is resolved; Camera check is not yet due). A rejected change can be shown with `PATCH /requests/3/status` `{"status":"resolved"}` on Camera check (409).

The same scenario is automated in `test_demo_scenario_summary` in `tests/test_reports.py`.

---

## Assumptions and decisions

- **Strict `YYYY-MM-DD` dates.** Only that exact format is accepted for `due_date` and `as_of`; timestamps, `20261029`, `29-10-2026` and impossible dates (`2026-02-30`) are rejected. Past due dates are accepted (so overdue items can be created and reported).
- **Strict types, no unknown fields.** The Pydantic models use `strict=True` and `extra="forbid"`: no silent coercion (`5` is not turned into `"5"`) and clients cannot set `id`, `status`, `created_at` or `resolved_at`.
- **`description`** is optional; omitted or `null` becomes `""`. Maximum 1000 characters. **`title`** is trimmed before the 5-100 length check.
- **Enums are case-sensitive lower case** (`Plumbing` is rejected).
- **Timestamps** are UTC, second precision, `YYYY-MM-DDTHH:MM:SSZ`. Because of the one-second resolution, `created_at` and `resolved_at` can be equal for a request resolved immediately.
- **422 for validation failures** (body, query, path, malformed JSON), including an invalid `as_of`; 404 for missing records; 409 for forbidden transitions.
- **Consistent error JSON** `{"error": {"code", "message", "details"}}` for every error, including framework-generated ones. `details` is included for validation errors only.
- **Importer header** must match `title,description,category,priority,due_date` exactly (order and case); a UTF-8 BOM is tolerated. Blank CSV lines are skipped. Importer exit code is 2 only when the file as a whole cannot be used.
- **`as_of` defaults to the current UTC date** when omitted.
- **Overdue** = `due_date < as_of` and not resolved; a request due on `as_of` is not overdue.
- **Repeating the current status** is a harmless 200 (idempotent) and never changes timestamps.
- **One SQLite connection per operation** (no shared connection across threads), with `BEGIN IMMEDIATE` for status changes so concurrent PATCHes cannot both pass the transition check.
- **No ORM**, by choice: one table and a handful of queries are clearer and easier to explain as plain parameterised SQL.
- **List ordering** is by `id` ascending (creation order), which makes pagination stable.
- **Database CHECK constraints** repeat the key rules (lengths, enum values) as defence in depth; the application validation is the primary check.

---

## Incomplete items, known limitations and future improvements

There are no known missing items from the brief: all five endpoints, the status rules, the shared validation, the CSV importer with sample data, the report and the tests are implemented. 

Known limitations (deliberate scope cuts):

- **No authentication or authorisation**; anyone who can reach the port can read and change data.
- **No way to edit or delete a request** other than its status (no `PUT`/`PATCH` for other fields, no `DELETE`).
- **No database migrations.** The schema is created with `CREATE TABLE IF NOT EXISTS`; changing it later would need a migration tool (e.g. Alembic or a versioned scripts folder).
- **Pagination has no `next`/`prev` links**, and uses offset paging, which can skip or repeat rows if data changes between pages.
- **SQLite is single-writer.** Fine for this scale; concurrent heavy writes would wait (10 s timeout) and a production deployment would use PostgreSQL.
- **Importer is not idempotent** (re-importing a file duplicates rows) and loads the whole file into memory before inserting.
- **Second-level timestamp resolution** and UTC only; no per-community time zone for "today".
- **No audit trail** of who changed a status or when each intermediate transition happened (only `resolved_at` is stored).
- **No structured logging, rate limiting or CORS configuration.**
- The `detail` text for framework-level errors (e.g. unknown route) is the framework default.

Possible next steps: an audit/history table for status changes, filters by due-date range, a `GET /reports/overdue` list, keyset pagination, an `Idempotency-Key` or natural-key duplicate check for imports, and a Dockerfile plus CI running `pytest`.

---

## AI and tool disclosure

This project was built **with assistance from Claude Code (Anthropic's Claude)**, which was used to scaffold the project, write and refactor the code, generate the tests and draft the documentation. The code was run and tested locally; the candidate is expected to be able to explain every part of it.

Libraries used: FastAPI, Starlette (via FastAPI), Pydantic v2, Uvicorn, httpx (test client transport), pytest, and the Python standard library (`sqlite3`, `csv`, `argparse`, `urllib`). No third-party code snippets were copied from other sources. See also [docs/EFFORT_LOG.md](docs/EFFORT_LOG.md).
