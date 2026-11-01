"""CSV importer tests. Every test uses its own temporary database and files."""
import csv
from pathlib import Path

import pytest

from app.db import RequestRepository
from app.importer import EXPECTED_HEADER, import_csv, main

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "sample_requests.csv"
HEADER = ",".join(EXPECTED_HEADER)


@pytest.fixture
def repo(db_path):
    repository = RequestRepository(db_path)
    repository.init_db()
    return repository


@pytest.fixture
def errors_path(tmp_path):
    return tmp_path / "errors.csv"


def write_csv(tmp_path, *lines, header=HEADER, name="input.csv", prefix=""):
    path = tmp_path / name
    path.write_text(prefix + "\n".join([header, *lines]) + "\n", encoding="utf-8", newline="")
    return path


def read_errors(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def all_rows(repo):
    return repo.list(page_size=100)[0]


GOOD = "Fix leaking tap,Drips at night,plumbing,high,2026-11-20"


# (a) sample file ------------------------------------------------------------------
def test_sample_csv_imports_three_and_rejects_two(repo, errors_path):
    assert all_rows(repo) == []  # starts from an empty database

    result = import_csv(SAMPLE_CSV, repo, errors_path)

    assert (result.imported, result.rejected, result.header_error) == (3, 2, None)
    rows = all_rows(repo)
    assert len(rows) == 3
    assert repo.list()[1] == 3
    assert all(r["status"] == "open" for r in rows)

    errors = read_errors(errors_path)
    assert [e["row_number"] for e in errors] == ["4", "6"]
    assert "title must not be blank" in errors[0]["reason"]
    assert errors[1]["reason"].startswith("category:")
    assert "electrical" in errors[1]["reason"]


# (b) header / file problems -------------------------------------------------------
@pytest.mark.parametrize(
    "header",
    [
        "title,description,category,priority",  # missing column
        "description,title,category,priority,due_date",  # wrong order
        "Title,Description,Category,Priority,Due_Date",  # wrong case
        "title,description,category,priority,due_date,extra",
    ],
)
def test_wrong_header_imports_nothing(tmp_path, repo, errors_path, capsys, header):
    path = write_csv(tmp_path, GOOD, header=header)

    code = main([str(path), "--db", str(repo.db_path), "--errors", str(errors_path)])

    assert code == 2
    assert all_rows(repo) == []
    errors = read_errors(errors_path)
    assert len(errors) == 1 and errors[0]["row_number"] == "1"
    assert "header" in errors[0]["reason"]
    captured = capsys.readouterr()
    assert "header" in captured.err
    assert "Imported" not in captured.out


def test_empty_file_imports_nothing(tmp_path, repo, errors_path, capsys):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")

    code = main([str(path), "--db", str(repo.db_path), "--errors", str(errors_path)])

    assert code == 2
    assert all_rows(repo) == []
    errors = read_errors(errors_path)
    assert len(errors) == 1 and errors[0]["row_number"] == "1"
    assert "empty" in errors[0]["reason"]
    assert "empty" in capsys.readouterr().err


def test_missing_file_returns_2(tmp_path, repo, errors_path, capsys):
    code = main([str(tmp_path / "nope.csv"), "--db", str(repo.db_path), "--errors", str(errors_path)])

    assert code == 2
    assert all_rows(repo) == []
    errors = read_errors(errors_path)
    assert len(errors) == 1 and errors[0]["row_number"] == "1"
    assert "not found" in errors[0]["reason"]
    assert "not found" in capsys.readouterr().err


def test_header_only_file_is_valid_and_empty(tmp_path, repo, errors_path):
    result = import_csv(write_csv(tmp_path), repo, errors_path)

    assert (result.imported, result.rejected, result.header_error) == (0, 0, None)
    assert read_errors(errors_path) == []
    assert errors_path.read_text(encoding="utf-8").strip() == "row_number,reason"


# (c) valid rows after invalid rows ------------------------------------------------
def test_valid_rows_after_invalid_rows_are_imported(tmp_path, repo, errors_path):
    path = write_csv(
        tmp_path,
        ",no title,plumbing,low,2026-11-01",  # row 2 invalid
        "Replace smoke alarm,,security,medium,2026-11-02",  # row 3 valid
        "Rewire garage,,electrical,urgent,2026-11-03",  # row 4 invalid
        "Check water heater,,plumbing,low,2026-11-04",  # row 5 valid
    )

    result = import_csv(path, repo, errors_path)

    assert (result.imported, result.rejected) == (2, 2)
    assert [r["title"] for r in all_rows(repo)] == ["Replace smoke alarm", "Check water heater"]
    assert all_rows(repo)[0]["description"] == ""  # empty cell means ""
    errors = read_errors(errors_path)
    assert [e["row_number"] for e in errors] == ["2", "4"]
    assert errors[1]["reason"].startswith("priority:")


def test_blank_lines_are_skipped_but_still_counted(tmp_path, repo, errors_path):
    path = write_csv(tmp_path, GOOD, "", "", ",bad,plumbing,low,2026-11-01")

    result = import_csv(path, repo, errors_path)

    assert (result.imported, result.rejected) == (1, 1)  # blank lines are not rejects
    assert [e["row_number"] for e in read_errors(errors_path)] == ["5"]


# (d) column count -----------------------------------------------------------------
def test_wrong_column_count_is_rejected(tmp_path, repo, errors_path):
    path = write_csv(
        tmp_path,
        "Too few columns,plumbing,low,2026-11-01",
        "Too many columns,desc,plumbing,low,2026-11-01,extra",
        GOOD,
    )

    result = import_csv(path, repo, errors_path)

    assert (result.imported, result.rejected) == (1, 2)
    errors = read_errors(errors_path)
    assert errors[0] == {"row_number": "2", "reason": "expected 5 columns, found 4"}
    assert errors[1] == {"row_number": "3", "reason": "expected 5 columns, found 6"}


# (e) duplicates -------------------------------------------------------------------
def test_duplicate_rows_create_separate_requests(tmp_path, repo, errors_path):
    result = import_csv(write_csv(tmp_path, GOOD, GOOD), repo, errors_path)

    assert result.imported == 2
    rows = all_rows(repo)
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]


# (f) BOM --------------------------------------------------------------------------
def test_bom_prefixed_header_is_accepted(tmp_path, repo, errors_path):
    path = write_csv(tmp_path, GOOD, prefix="﻿")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")

    result = import_csv(path, repo, errors_path)

    assert (result.imported, result.rejected, result.header_error) == (1, 0, None)


# (g) same rules as the API --------------------------------------------------------
@pytest.mark.parametrize(
    "row, field",
    [
        ("abcd,,plumbing,low,2026-11-01", "title"),  # 4 chars
        (f"{'x' * 101},,plumbing,low,2026-11-01", "title"),  # 101 chars
        ("Valid title,,plumbing,low,2026-02-30", "due_date"),  # not a real date
        ("Valid title,,plumbing,low,01/11/2026", "due_date"),  # wrong format
        (f"Valid title,{'d' * 1001},plumbing,low,2026-11-01", "description"),
        ("Valid title,,heating,low,2026-11-01", "category"),
        ("Valid title,,plumbing,urgent,2026-11-01", "priority"),
        ("     ,,plumbing,low,2026-11-01", "title"),  # whitespace-only title
    ],
)
def test_importer_rejects_what_the_api_rejects(tmp_path, repo, errors_path, row, field):
    result = import_csv(write_csv(tmp_path, row), repo, errors_path)

    assert (result.imported, result.rejected) == (0, 1)
    assert read_errors(errors_path)[0]["reason"].startswith(f"{field}:")


def test_boundary_values_past_dates_and_trimming_are_accepted(tmp_path, repo, errors_path):
    path = write_csv(
        tmp_path,
        f"{'x' * 5},,plumbing,low,2026-11-01",  # exactly 5 chars
        f"{'y' * 100},{'d' * 1000},security,high,2026-11-01",  # exact maximums
        "  Padded title  ,,electrical,medium,2020-01-01",  # past due date, spaces around title
    )

    result = import_csv(path, repo, errors_path)

    assert (result.imported, result.rejected) == (3, 0)
    rows = all_rows(repo)
    assert rows[2]["title"] == "Padded title"
    assert rows[2]["due_date"] == "2020-01-01"


def test_importer_and_api_give_same_reason(client, tmp_path, repo, errors_path):
    payload = {
        "title": "abc",
        "description": "",
        "category": "garden",
        "priority": "low",
        "due_date": "2026-02-30",
    }
    api_message = client.post("/requests", json=payload).json()["error"]["message"]

    line = ",".join([payload["title"], payload["description"], payload["category"], payload["priority"], payload["due_date"]])
    import_csv(write_csv(tmp_path, line), repo, errors_path)

    assert read_errors(errors_path)[0]["reason"] == api_message


# (h) CLI end to end ---------------------------------------------------------------
def test_cli_end_to_end(tmp_path, db_path, errors_path, capsys):
    code = main([str(SAMPLE_CSV), "--db", str(db_path), "--errors", str(errors_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Imported: 3" in out
    assert "Rejected: 2" in out
    assert f"Errors written to: {errors_path}" in out
    # Data persisted: visible through a brand new repository object.
    items, total = RequestRepository(db_path).list()
    assert total == 3 and len(items) == 3
    assert len(read_errors(errors_path)) == 2


def test_cli_without_rejects_omits_errors_line_and_writes_header_only(tmp_path, db_path, errors_path, capsys):
    path = write_csv(tmp_path, GOOD)

    code = main([str(path), "--db", str(db_path), "--errors", str(errors_path)])

    out = capsys.readouterr().out
    assert code == 0 and "Imported: 1" in out and "Rejected: 0" in out
    assert "Errors written to" not in out
    assert read_errors(errors_path) == []


def test_cli_creates_database_in_new_location(tmp_path, errors_path):
    db = tmp_path / "nested" / "new.db"

    assert main([str(SAMPLE_CSV), "--db", str(db), "--errors", str(errors_path)]) == 0
    assert RequestRepository(db).list()[1] == 3
