# Design note

A short explanation of how the Service Request Management API is put together and, in particular, how one set of validation rules serves both the HTTP API and the CSV importer.

## 1. Architecture and layers

```
  HTTP client                         command line
      |                                    |
  app/main.py  (routes, error handlers)  app/importer.py (CSV parsing, error report)
      \                                    /
       \--------- app/validation.py -------/     one RequestCreate model + error formatting
                         |
                    app/db.py                    RequestRepository: ALL SQL, transitions, summary
                         |
                  SQLite file (data/requests.db, or SRM_DB_PATH)
```

| Module | Responsibility |
|---|---|
| `app/config.py` | Resolves the database path (`SRM_DB_PATH` or `data/requests.db`). |
| `app/validation.py` | The `RequestCreate` and `StatusUpdate` Pydantic models, `parse_iso_date`, and the shared error helpers `format_errors` and `error_summary`. No I/O. |
| `app/db.py` | Schema, `RequestRepository` (create, create_many, get, list, update_status, summary), `ALLOWED_TRANSITIONS`, `TransitionError`. The only place that contains SQL. |
| `app/main.py` | `create_app(db_path)` factory: routes, the error envelope and exception handlers. Contains no business logic beyond calling the repository. |
| `app/importer.py` | CSV reading, header check, per-row validation, error report, CLI. Reuses `validation.py` and `RequestRepository`. |

Keeping SQL in one class and rules in one module means a rule change happens in one place, and the tests can exercise each layer directly (`import_csv(...)` with a repository, or the API through `TestClient`).

## 2. Data model

One table, `requests`:

| Column | Type | Constraint |
|---|---|---|
| `id` | INTEGER | primary key, autoincrement |
| `title` | TEXT | NOT NULL, `CHECK (length(title) BETWEEN 5 AND 100)` |
| `description` | TEXT | NOT NULL, default `''`, `CHECK (length(description) <= 1000)` |
| `category` | TEXT | NOT NULL, `CHECK (category IN ('electrical','plumbing','security'))` |
| `priority` | TEXT | NOT NULL, `CHECK (priority IN ('low','medium','high'))` |
| `status` | TEXT | NOT NULL, default `'open'`, `CHECK (status IN ('open','in_progress','resolved'))` |
| `created_at` | TEXT | NOT NULL, UTC `YYYY-MM-DDTHH:MM:SSZ`, set by the server |
| `due_date` | TEXT | NOT NULL, `YYYY-MM-DD` |
| `resolved_at` | TEXT | NULL until resolved |

Indexes on `status` and `due_date` support filtering and the overdue count. The allowed values in the `CHECK` clauses are generated from the same `CATEGORIES`, `PRIORITIES` and `STATUSES` tuples that the Pydantic types use, so they cannot drift apart. The CHECK constraints are **defence in depth**: the application validates first, but a bug or a manual SQL insert still cannot put an invalid enum value or an over-long title into the table. Dates are stored as ISO text, which sorts lexically, so `due_date < ?` is a correct date comparison without date functions.

## 3. Shared validation (API and importer)

**Problem.** The brief requires the importer to follow "the same creation rules as the API". Two separate implementations would eventually disagree.

**Design.** There is exactly one definition of a valid new request: `RequestCreate` in `app/validation.py`.

- The API route is declared as `def create_request(payload: RequestCreate)`, so FastAPI validates the JSON body against it.
- The importer builds `dict(zip(EXPECTED_HEADER, row))` for each CSV line and calls `RequestCreate.model_validate(...)` on it.

Both paths therefore apply the same field rules, with the same constraints:

- `title`: trimmed, 5-100 characters, not blank;
- `description`: optional, at most 1000 characters; `None`/omitted becomes `""`;
- `category` and `priority`: `Literal` enums;
- `due_date`: strict `YYYY-MM-DD` and a real calendar date, checked by the shared `parse_iso_date`;
- `ConfigDict(extra="forbid", strict=True)`: unknown or server-owned keys (`id`, `status`, `created_at`, `resolved_at`) are errors, and no silent coercion happens. Every CSV cell is a string, which is exactly the type the strict model expects for all five fields, so the same model works unchanged for both input forms; for JSON it additionally blocks things like `{"title": 12345}`.

**Shared error formatting.** Pydantic errors are converted by `format_errors` into `[{"field", "message"}]` (it strips `body`/`query` prefixes, removes the "Value error, " prefix and gives a friendly message for forbidden extra keys). `error_summary` joins them into `field: message; field: message`. The API's 422 handler puts the list in `error.details` and the summary in `error.message`; the importer writes the summary as the `reason` column of the errors CSV. A test (`test_importer_and_api_give_same_reason`) asserts that the same bad input yields the identical text through both entry points.

**What this guarantees.** A record is accepted by the importer if and only if the same values would be accepted by `POST /requests`, and a rejection explains itself in the same words. Anything the CSV format cannot express is handled in the importer only: the header check and the column count per row. Both entry points hand the validated object to the same `RequestRepository` insert, so server-set values (`status = 'open'`, `created_at`) are also identical.

## 4. Status transitions

```python
ALLOWED_TRANSITIONS = {"open": "in_progress", "in_progress": "resolved"}
```

`RequestRepository.update_status` runs on a dedicated connection in autocommit mode and issues `BEGIN IMMEDIATE`. That takes SQLite's write lock before the current status is read, so two concurrent PATCH calls on the same request are serialised: the second sees the first one's result and is judged against it (a "read, decide, write" sequence with no gap in which another writer can change the row).

Then, within the lock:

1. record not found -> rollback, return `None` (route returns 404);
2. requested status equals the current one -> rollback and return the row untouched (**idempotent repeat**: HTTP 200, `resolved_at` and everything else unchanged);
3. `ALLOWED_TRANSITIONS.get(current) != requested` -> rollback, raise `TransitionError` (HTTP 409 `invalid_transition`);
4. otherwise `UPDATE` the status, setting `resolved_at` to the current UTC time when the new status is `resolved`; commit.

A single dictionary is the whole workflow, which makes the rule trivially reviewable and easy to extend.

## 5. Overdue and the summary report

`GET /reports/summary?as_of=` parses `as_of` with the strict date parser (defaulting to today's UTC date) and the repository returns:

- `status_counts` and `priority_counts` seeded with all keys at 0, then filled from `GROUP BY` queries, so keys never disappear when a bucket is empty;
- `total` = sum of the status counts;
- `overdue_count` = `COUNT(*) WHERE due_date < :as_of AND status != 'resolved'`.

The comparison is strictly less-than, so **a request due on `as_of` is not overdue** (the boundary is covered by the `test_overdue_boundary_*` tests). Resolved requests are never overdue, whatever their due date. Passing `as_of` explicitly makes the report deterministic and testable regardless of the real clock.

## 6. Error contract

All errors are `{"error": {"code", "message", "details?"}}`, produced by one `error_response` helper and three handlers: `RequestValidationError` (422, `validation_error`), Starlette `HTTPException` (404 `not_found`, 405, other) and `TransitionError` (409, `invalid_transition`). FastAPI's default `{"detail": ...}` shape therefore never leaks out. `details` is included only when there are items to list (validation errors). 422 is used for every validation problem, including malformed JSON and a bad `as_of`, so clients need to learn one rule: 4xx with a code means "fix your request".

## 7. Importer design

1. **Header first.** The first CSV record must equal `title,description,category,priority,due_date`. An empty file, a missing file, an unreadable file or any header mismatch yields a "header error": nothing is imported, the reason is written to the errors file as row 1, and the CLI exits 2.
2. **Row numbers** follow the file: header = row 1, first data line = row 2. Blank lines are skipped but still counted, so reported numbers match an editor or spreadsheet.
3. **Per row:** wrong column count -> reject (`expected 5 columns, found N`); otherwise `RequestCreate.model_validate`; failures are collected as `(row_number, reason)`.
4. **Insert:** all valid rows go through `RequestRepository.create_many` in a **single transaction** (one commit, much faster than a commit per row, and all-or-nothing if the database itself fails). Because validation happens before the insert, rejected rows can never abort the valid ones.
5. **Report:** `Imported`, `Rejected` counts are printed; `row_number,reason` CSV is always written (header only when nothing was rejected).

## 8. Testing strategy

- `create_app(db_path)` is a factory; the `client` fixture passes a per-test `tmp_path` database, so tests are isolated, parallel-safe and never touch `data/requests.db`.
- The suite covers validation boundaries (4/5/100/101 character titles, 1000/1001 descriptions, impossible dates), every forbidden transition, idempotent repeats (with a fake clock to prove timestamps do not change), pagination edges, the overdue boundary, the importer (sample file, header errors, blank lines, row numbering, BOM, same-reason-as-API) and persistence across app restarts.
- Reports are tested with an explicit `as_of` so results do not depend on the day the tests run (the two tests that use the default `as_of` compare against the live UTC date).

## 9. Trade-offs

| Decision | Benefit | Cost |
|---|---|---|
| Plain `sqlite3`, no ORM | Little code, SQL is visible and easy to defend in an interview | Hand-written SQL; no migrations tooling |
| New connection per operation | Thread-safe with FastAPI's thread pool; no shared state | Slight connection overhead (negligible for SQLite) |
| `BEGIN IMMEDIATE` for PATCH | Correct under concurrency | Writers wait on each other; acceptable for SQLite |
| Strict + `extra="forbid"` | Predictable contract, clear errors | Less forgiving to sloppy clients (by design) |
| 422 for all validation | One consistent rule | Some teams prefer 400; documented in README |
| Offset pagination | Simple, matches the brief | Not stable under concurrent inserts at scale |
| Dates and timestamps as text | Portable, lexical ordering works | No DB-side date type enforcement beyond the application check |
