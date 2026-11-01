"""Command line CSV importer.

Usage:  python -m app.importer <csv_path> [--db PATH] [--errors PATH]

Every row is validated with the same `RequestCreate` model the API uses. Valid
rows are inserted in one transaction; invalid rows are reported (row number plus
reason) in an errors CSV and never stop the rest of the file from importing.

Exit codes: 0 = file processed (rejected rows are still a normal outcome),
2 = the file could not be used at all (missing, empty, unreadable, wrong header).
"""
import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .config import get_db_path
from .db import RequestRepository
from .validation import RequestCreate, error_summary, format_errors

EXPECTED_HEADER = ["title", "description", "category", "priority", "due_date"]
DEFAULT_ERRORS_PATH = "import_errors.csv"


@dataclass
class ImportResult:
    """Outcome of one import run."""

    imported: int = 0
    rejected: int = 0
    header_error: Optional[str] = None


def validate_row(row: list[str]) -> tuple[Optional[RequestCreate], Optional[str]]:
    """Return (request, None) for a valid row or (None, reason) for an invalid one."""
    if len(row) != len(EXPECTED_HEADER):
        return None, f"expected {len(EXPECTED_HEADER)} columns, found {len(row)}"
    try:
        return RequestCreate.model_validate(dict(zip(EXPECTED_HEADER, row))), None
    except ValidationError as exc:
        return None, error_summary(format_errors(exc.errors()))


def write_errors(errors_path: Path | str, errors: list[tuple[int, str]]) -> None:
    """Write the `row_number,reason` report (header only when there are no errors)."""
    with open(errors_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["row_number", "reason"])
        writer.writerows(errors)


def import_csv(csv_path: Path | str, repo: RequestRepository, errors_path: Path | str) -> ImportResult:
    """Import `csv_path` into `repo` and write rejected rows to `errors_path`."""
    repo.init_db()
    valid: list[RequestCreate] = []
    errors: list[tuple[int, str]] = []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                header_error = "file is empty; expected header: " + ",".join(EXPECTED_HEADER)
            elif header != EXPECTED_HEADER:
                header_error = (
                    f"invalid header: expected '{','.join(EXPECTED_HEADER)}' "
                    f"but found '{','.join(header)}'"
                )
            else:
                header_error = None
                # Header is row 1, so the first data record is row 2.
                for row_number, row in enumerate(reader, start=2):
                    if not row:  # completely blank line: skipped, not an error
                        continue
                    request, reason = validate_row(row)
                    if request is not None:
                        valid.append(request)
                    else:
                        errors.append((row_number, reason))
    except FileNotFoundError:
        header_error = f"file not found: {csv_path}"
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        header_error = f"could not read file: {exc}"

    if header_error:
        write_errors(errors_path, [(1, header_error)])
        return ImportResult(header_error=header_error)

    imported = repo.create_many(valid) if valid else 0
    write_errors(errors_path, errors)
    return ImportResult(imported=imported, rejected=len(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.importer", description="Import service requests from a CSV file.")
    parser.add_argument("csv_path", help="CSV file with header: " + ",".join(EXPECTED_HEADER))
    parser.add_argument("--db", default=None, help="SQLite database path (default: SRM_DB_PATH or data/requests.db)")
    parser.add_argument("--errors", default=DEFAULT_ERRORS_PATH, help="where to write rejected rows (default: %(default)s)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    repo = RequestRepository(args.db or get_db_path())
    result = import_csv(args.csv_path, repo, args.errors)
    if result.header_error:
        print(f"Import failed: {result.header_error}", file=sys.stderr)
        print(f"Nothing was imported. Details written to: {args.errors}", file=sys.stderr)
        return 2
    print(f"Imported: {result.imported}")
    print(f"Rejected: {result.rejected}")
    if result.rejected:
        print(f"Errors written to: {args.errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
