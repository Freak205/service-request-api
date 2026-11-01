"""FastAPI application: routes and error handling."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_db_path
from .db import RequestRepository, TransitionError
from .validation import (
    Category,
    Priority,
    RequestCreate,
    Status,
    StatusUpdate,
    error_summary,
    format_errors,
    parse_iso_date,
)


def error_response(status_code: int, code: str, message: str, details: Optional[list] = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def create_app(db_path: Optional[Path | str] = None) -> FastAPI:
    """Build the app. Passing db_path lets tests use an isolated database."""
    repo = RequestRepository(db_path or get_db_path())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repo.init_db()  # create the database file/schema when the server starts
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="Service Request Management API",
        description="Record maintenance requests, track status changes and report pending work.",
        version="1.0.0",
    )
    app.state.repo = repo

    # ---- error handling ---------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        details = format_errors(exc.errors())
        return error_response(422, "validation_error", error_summary(details), details)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(TransitionError)
    async def transition_handler(_: Request, exc: TransitionError):
        return error_response(
            409,
            "invalid_transition",
            f"{exc}. Allowed transitions: open -> in_progress -> resolved.",
        )

    def not_found(request_id: int) -> JSONResponse:
        return error_response(404, "not_found", f"Request {request_id} does not exist.")

    # ---- routes -----------------------------------------------------------------
    @app.post("/requests", status_code=201)
    def create_request(payload: RequestCreate):
        return repo.create(payload)

    @app.get("/requests")
    def list_requests(
        category: Optional[Category] = None,
        priority: Optional[Priority] = None,
        status: Optional[Status] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    ):
        items, total = repo.list(category, priority, status, page, page_size)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.get("/requests/{request_id}")
    def get_request(request_id: int):
        row = repo.get(request_id)
        return row if row else not_found(request_id)

    @app.patch("/requests/{request_id}/status")
    def change_status(request_id: int, payload: StatusUpdate):
        row = repo.update_status(request_id, payload.status)
        return row if row else not_found(request_id)

    @app.get("/reports/summary")
    def summary(as_of: Optional[str] = None):
        if as_of is None:
            as_of_date = datetime.now(timezone.utc).date()
        else:
            try:
                as_of_date = parse_iso_date(as_of)
            except ValueError as exc:
                details = [{"field": "as_of", "message": str(exc)}]
                return error_response(422, "validation_error", error_summary(details), details)
        return repo.summary(as_of_date)

    return app


app = create_app()
