"""Shared creation rules.

`RequestCreate` is the single definition of what a valid new request looks like.
The API validates request bodies with it and the CSV importer validates each row
with it, so both entry points apply exactly the same rules and error wording.
"""
import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Category = Literal["electrical", "plumbing", "security"]
Priority = Literal["low", "medium", "high"]
Status = Literal["open", "in_progress", "resolved"]

CATEGORIES: tuple[str, ...] = ("electrical", "plumbing", "security")
PRIORITIES: tuple[str, ...] = ("low", "medium", "high")
STATUSES: tuple[str, ...] = ("open", "in_progress", "resolved")

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value: str) -> date:
    """Parse a strict YYYY-MM-DD string (rejects timestamps, `20261029`, etc.)."""
    if not isinstance(value, str) or not ISO_DATE_RE.match(value):
        raise ValueError("must be an ISO date in YYYY-MM-DD format")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("must be a real calendar date in YYYY-MM-DD format") from None


class RequestCreate(BaseModel):
    """Fields a client may supply when creating a request.

    Anything else (id, status, created_at, resolved_at, unknown keys) is rejected
    by `extra="forbid"`. `strict=True` stops silent coercion such as 5 -> "5".
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] = ""
    category: Category
    priority: Priority
    due_date: str

    @field_validator("title", mode="before")
    @classmethod
    def _title_not_blank(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _null_description_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("due_date")
    @classmethod
    def _due_date_is_iso(cls, value: str) -> str:
        parse_iso_date(value)
        return value


class StatusUpdate(BaseModel):
    """Body of PATCH /requests/{id}/status."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Status


def format_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Flatten pydantic error dicts into [{"field": ..., "message": ...}]."""
    details = []
    for err in errors:
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body", "query")]
        message = str(err.get("msg", "invalid value"))
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        if err.get("type") == "extra_forbidden":
            message = "unsupported field; it cannot be supplied by the client"
        field = ".".join(loc) or "body"
        if err.get("type") == "json_invalid":
            field, message = "body", "request body is not valid JSON"
        details.append({"field": field, "message": message})
    return details


def error_summary(details: list[dict[str, str]]) -> str:
    """One-line human readable form, used for the CSV importer's reject reasons."""
    return "; ".join(f"{d['field']}: {d['message']}" for d in details)
