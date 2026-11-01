# Effort log

**Candidate:** Ineedi Venkata Sai Anirudh  |  **Assignment date:** 31-10-2026

| Activity | Planned hours | Actual hours | What was done in each activity |
|---|---|---|---|
| Design and project setup | 1.0 | 0.50 | Read the requirements; chose FastAPI + plain `sqlite3` + Pydantic v2; designed the layers (`validation` / `db` / `main` / `importer`), the status-transition rule, the overdue definition (`due_date < as_of`, not resolved) and the `{"error": {...}}` contract; project layout, `requirements.txt`, `.gitignore`, `pytest.ini`. Captured in `docs/DESIGN.md`. |
| Persistence and validation | 2.0 | 0.75 | `app/config.py`; `app/db.py` (schema with CHECK constraints, `RequestRepository`, `BEGIN IMMEDIATE` status changes); `app/validation.py` (single `RequestCreate` model with strict mode and `extra="forbid"`, shared `format_errors` / `error_summary`). |
| API operations and reporting | 3.0 | 1.00 | `app/main.py` (`create_app` factory, five endpoints, AND filters + pagination, error handlers) and the summary/overdue query in `app/db.py`. |
| CSV import and error handling | 1.5 | 0.50 | `app/importer.py` (header check first, header = row 1, single-transaction insert, error CSV, exit codes); `sample_data/sample_requests.csv` (3 valid + 2 invalid rows) and the matching `sample_data/import_errors.csv`. |
| Automated tests and debugging | 2.0 | 0.75 | 144 pytest tests in `tests/`: `test_api.py` (92), `test_reports.py` (19, incl. the three-record demo and overdue boundary), `test_importer.py` (26), `test_persistence.py` (7). Each test uses an isolated temp database via `create_app(db_path)`. Debugging: made schema creation lazy so importing the app has no side effects. |
| README and demo preparation | 1.5 | 0.50 | `README.md` (setup, run and test commands, reset, sample calls, assumptions, incomplete items), `docs/DESIGN.md` and this log. The three-record demo is documented in the README and covered by a test. |
| **Total** | **11.0** | **4.00** | |

The planned hours (1.0, 2.0, 3.0, 1.5, 2.0, 1.5 = 11.0) and the activity names are from the brief. Actual effort was 4.00 hours in total, within the 12-hour limit.

## Tools used / disclosure

- **Claude Code (Anthropic Claude)** was used to scaffold the project, implement the code, write the tests and draft the documentation. The candidate reviewed the output and is responsible for being able to explain it.
- **Libraries:** FastAPI (with Starlette), Pydantic v2, Uvicorn, httpx (used by FastAPI's `TestClient`), pytest; Python standard library `sqlite3`, `csv`, `argparse`, `urllib`.
- **No external code snippets** were copied from Stack Overflow, blogs or other repositories.

## Remaining work

- No functional items from the brief are known to be incomplete: all endpoints, the status rules, the shared validation, the CSV importer with sample data, the report, the tests and the documents exist, and the 144 tests passed when last run.
- Deliberate scope limits (no auth, no edit/delete endpoints other than status, no migrations, offset pagination only) are listed under "Incomplete items, known limitations and future improvements" in `README.md`.
- Before the interview the candidate should read the code and tests in full and prepare the 15-minute walkthrough.
