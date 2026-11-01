"""API tests: creation, validation, retrieval, filtering, pagination and status transitions."""
import re

import pytest

from .conftest import VALID_PAYLOAD

UTC_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def assert_error_shape(response, status, code=None):
    """The body must be {"error": {"code", "message", ...}} with the given status."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    if code:
        assert body["error"]["code"] == code
    return body["error"]


def total_records(client):
    return client.get("/requests").json()["total"]


# --------------------------------------------------------------------------- creation
def test_create_returns_201_and_stored_record(client):
    """POST a valid body returns 201 and the record exactly as it can be fetched later."""
    response = client.post("/requests", json=VALID_PAYLOAD)
    assert response.status_code == 201
    created = response.json()
    assert created["title"] == "Water leakage"
    assert created["category"] == "plumbing"
    assert created["priority"] == "high"
    assert created["due_date"] == "2026-10-29"
    assert client.get(f"/requests/{created['id']}").json() == created


def test_create_sets_server_defaults(client):
    """New records start open, unresolved, with an integer id and a UTC created_at ending in Z."""
    created = client.post("/requests", json=VALID_PAYLOAD).json()
    assert isinstance(created["id"], int) and not isinstance(created["id"], bool)
    assert created["status"] == "open"
    assert created["resolved_at"] is None
    assert UTC_TS.match(created["created_at"])
    assert created["created_at"].endswith("Z")


def test_create_ids_increase(client, make_request):
    """Each new request gets a distinct, increasing id."""
    ids = [make_request()["id"] for _ in range(3)]
    assert ids == sorted(ids) and len(set(ids)) == 3


def test_create_trims_title(client):
    """Leading and trailing whitespace is removed from the stored title."""
    created = client.post("/requests", json={**VALID_PAYLOAD, "title": "   Broken lift  "}).json()
    assert created["title"] == "Broken lift"


def test_create_without_description_defaults_to_empty(client):
    """Description is optional."""
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "description"}
    response = client.post("/requests", json=payload)
    assert response.status_code == 201
    assert response.json()["description"] == ""


@pytest.mark.parametrize("title", ["abcde", "x" * 100])
def test_create_accepts_title_length_boundaries(client, title):
    """Titles of exactly 5 and 100 characters are valid."""
    assert client.post("/requests", json={**VALID_PAYLOAD, "title": title}).status_code == 201


def test_create_accepts_description_of_1000_chars(client):
    """A 1000 character description is the maximum allowed."""
    response = client.post("/requests", json={**VALID_PAYLOAD, "description": "d" * 1000})
    assert response.status_code == 201
    assert len(response.json()["description"]) == 1000


def test_create_accepts_past_due_date(client):
    """A due date in the past is accepted."""
    response = client.post("/requests", json={**VALID_PAYLOAD, "due_date": "2000-01-01"})
    assert response.status_code == 201
    assert response.json()["due_date"] == "2000-01-01"


# ------------------------------------------------------------------------ validation
INVALID_BODIES = {
    "blank_title": {**VALID_PAYLOAD, "title": ""},
    "whitespace_title": {**VALID_PAYLOAD, "title": "      "},
    "title_4_chars": {**VALID_PAYLOAD, "title": "abcd"},
    "title_4_chars_after_trim": {**VALID_PAYLOAD, "title": "  abcd  "},
    "title_101_chars": {**VALID_PAYLOAD, "title": "x" * 101},
    "title_not_string_int": {**VALID_PAYLOAD, "title": 12345},
    "title_null": {**VALID_PAYLOAD, "title": None},
    "title_list": {**VALID_PAYLOAD, "title": ["Water leakage"]},
    "description_1001_chars": {**VALID_PAYLOAD, "description": "d" * 1001},
    "description_not_string": {**VALID_PAYLOAD, "description": 42},
    "invalid_category": {**VALID_PAYLOAD, "category": "gardening"},
    "category_wrong_case": {**VALID_PAYLOAD, "category": "Plumbing"},
    "invalid_priority": {**VALID_PAYLOAD, "priority": "urgent"},
    "due_date_month_13": {**VALID_PAYLOAD, "due_date": "2026-13-01"},
    "due_date_feb_30": {**VALID_PAYLOAD, "due_date": "2026-02-30"},
    "due_date_day_first": {**VALID_PAYLOAD, "due_date": "29-10-2026"},
    "due_date_compact": {**VALID_PAYLOAD, "due_date": "20261029"},
    "due_date_timestamp": {**VALID_PAYLOAD, "due_date": "2026-10-29T10:00:00"},
    "due_date_empty": {**VALID_PAYLOAD, "due_date": ""},
    "due_date_not_string": {**VALID_PAYLOAD, "due_date": 20261029},
    "missing_title": {k: v for k, v in VALID_PAYLOAD.items() if k != "title"},
    "missing_category": {k: v for k, v in VALID_PAYLOAD.items() if k != "category"},
    "missing_priority": {k: v for k, v in VALID_PAYLOAD.items() if k != "priority"},
    "missing_due_date": {k: v for k, v in VALID_PAYLOAD.items() if k != "due_date"},
    "empty_object": {},
    "client_supplied_id": {**VALID_PAYLOAD, "id": 99},
    "client_supplied_status": {**VALID_PAYLOAD, "status": "resolved"},
    "client_supplied_created_at": {**VALID_PAYLOAD, "created_at": "2020-01-01T00:00:00Z"},
    "client_supplied_resolved_at": {**VALID_PAYLOAD, "resolved_at": "2020-01-01T00:00:00Z"},
    "unknown_field": {**VALID_PAYLOAD, "assignee": "bob"},
}


@pytest.mark.parametrize("body", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_create_rejects_invalid_body(client, body):
    """Invalid creation bodies return a 422 error JSON and store nothing."""
    response = client.post("/requests", json=body)
    error = assert_error_shape(response, 422, "validation_error")
    assert error["details"], "validation errors should say which field failed"
    assert total_records(client) == 0


@pytest.mark.parametrize(
    "content",
    ['{"title": "Water leakage", ', "not json at all", ""],
    ids=["truncated_json", "plain_text", "empty_body"],
)
def test_create_rejects_malformed_json(client, content):
    """A body that is not parseable JSON returns 422 and creates nothing."""
    response = client.post("/requests", content=content, headers={"Content-Type": "application/json"})
    assert_error_shape(response, 422, "validation_error")
    assert total_records(client) == 0


@pytest.mark.parametrize("body", [[VALID_PAYLOAD], "Water leakage", 42, None], ids=["array", "string", "number", "null"])
def test_create_rejects_non_object_json(client, body):
    """Valid JSON that is not an object returns 422 and creates nothing."""
    assert_error_shape(client.post("/requests", json=body), 422, "validation_error")
    assert total_records(client) == 0


def test_validation_error_reports_each_bad_field(client):
    """Details name every invalid field when several are wrong at once."""
    body = {**VALID_PAYLOAD, "category": "nope", "due_date": "bad"}
    error = assert_error_shape(client.post("/requests", json=body), 422)
    assert {d["field"] for d in error["details"]} == {"category", "due_date"}


# -------------------------------------------------------------------------- retrieval
def test_get_missing_request_returns_404_error_json(client):
    """GET of an unknown id returns 404 with the standard error body."""
    assert_error_shape(client.get("/requests/9999"), 404, "not_found")


def test_get_non_integer_id_returns_422(client):
    """A non-numeric id is a validation error, not a 404."""
    assert_error_shape(client.get("/requests/abc"), 422, "validation_error")


def test_get_existing_request(client, make_request):
    """GET by id returns the stored record."""
    created = make_request(title="Gate light failure")
    response = client.get(f"/requests/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


# --------------------------------------------------------------------------- filtering
@pytest.fixture
def mixed_requests(client, make_request):
    """Six requests covering several category/priority/status combinations."""
    specs = [
        ("plumbing", "high"),      # 1 -> in_progress
        ("plumbing", "high"),      # 2 (open)
        ("plumbing", "low"),       # 3
        ("electrical", "high"),    # 4 -> in_progress
        ("security", "medium"),    # 5
        ("electrical", "low"),     # 6
    ]
    records = [make_request(title=f"Request number {i}", category=c, priority=p) for i, (c, p) in enumerate(specs, 1)]
    for index in (0, 3):
        client.patch(f"/requests/{records[index]['id']}/status", json={"status": "in_progress"})
    return records


def ids_of(response):
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_filter_by_single_field(client, mixed_requests):
    """Each filter works on its own."""
    assert ids_of(client.get("/requests", params={"category": "plumbing"})) == [1, 2, 3]
    assert ids_of(client.get("/requests", params={"priority": "high"})) == [1, 2, 4]
    assert ids_of(client.get("/requests", params={"status": "in_progress"})) == [1, 4]


def test_filters_combine_with_and(client, mixed_requests):
    """category + priority + status must all match."""
    response = client.get("/requests", params={"category": "plumbing", "priority": "high", "status": "in_progress"})
    assert ids_of(response) == [1]
    assert response.json()["total"] == 1
    response = client.get("/requests", params={"category": "plumbing", "priority": "high"})
    assert ids_of(response) == [1, 2]
    response = client.get("/requests", params={"category": "electrical", "status": "open"})
    assert ids_of(response) == [6]


def test_filter_without_matches_returns_empty_page(client, mixed_requests):
    """No matches gives total 0 and an empty items list (not an error)."""
    response = client.get("/requests", params={"category": "security", "priority": "high"})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [] and body["total"] == 0


@pytest.mark.parametrize(
    "params",
    [{"category": "gardening"}, {"priority": "urgent"}, {"status": "closed"}],
    ids=["category", "priority", "status"],
)
def test_filter_with_invalid_enum_returns_422(client, params):
    """Unknown filter values are rejected."""
    assert_error_shape(client.get("/requests", params=params), 422, "validation_error")


# -------------------------------------------------------------------------- pagination
@pytest.fixture
def twenty_five(client, make_request):
    return [make_request(title=f"Request number {i:02d}") for i in range(1, 26)]


def test_pagination_defaults(client, twenty_five):
    """Default page is 1 and default page_size is 10."""
    body = client.get("/requests").json()
    assert (body["page"], body["page_size"], body["total"]) == (1, 10, 25)
    assert [i["id"] for i in body["items"]] == list(range(1, 11))


@pytest.mark.parametrize(
    "page, expected_ids",
    [(1, list(range(1, 11))), (2, list(range(11, 21))), (3, list(range(21, 26)))],
)
def test_pagination_default_size_pages(client, twenty_five, page, expected_ids):
    """Pages 1-3 of 25 rows hold 10, 10 and 5 items in ascending id order."""
    body = client.get("/requests", params={"page": page}).json()
    assert [i["id"] for i in body["items"]] == expected_ids
    assert body["total"] == 25 and body["page"] == page


def test_pagination_custom_page_size(client, twenty_five):
    """A custom page_size changes the slice and is echoed back."""
    body = client.get("/requests", params={"page": 2, "page_size": 7}).json()
    assert [i["id"] for i in body["items"]] == list(range(8, 15))
    assert (body["page"], body["page_size"], body["total"]) == (2, 7, 25)


def test_pagination_pages_cover_all_rows_without_overlap(client, twenty_five):
    """Walking every page returns each record once, ordered by id ascending."""
    seen = []
    for page in range(1, 6):
        seen += [i["id"] for i in client.get("/requests", params={"page": page, "page_size": 6}).json()["items"]]
    assert seen == list(range(1, 26))


def test_pagination_page_beyond_end_is_empty(client, twenty_five):
    """A page past the last row returns empty items but the true total."""
    body = client.get("/requests", params={"page": 4}).json()
    assert body["items"] == [] and body["total"] == 25 and body["page"] == 4


def test_pagination_on_empty_database(client):
    """An empty database returns an empty first page."""
    body = client.get("/requests").json()
    assert body["items"] == [] and body["total"] == 0


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}, {"page": "x"}, {"page_size": "x"}],
    ids=["page0", "page_negative", "size0", "size101", "page_text", "size_text"],
)
def test_pagination_rejects_invalid_values(client, params):
    """Out-of-range or non-numeric paging parameters return 422."""
    assert_error_shape(client.get("/requests", params=params), 422, "validation_error")


def test_pagination_accepts_page_size_100(client, twenty_five):
    """page_size=100 is the largest allowed value."""
    response = client.get("/requests", params={"page_size": 100})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 25


# ------------------------------------------------------------------ status transitions
def patch_status(client, request_id, status):
    return client.patch(f"/requests/{request_id}/status", json={"status": status})


@pytest.fixture
def fake_clock(monkeypatch):
    """Make utc_now_iso return a different second on every call (timestamps have 1s resolution)."""
    state = {"n": 0}

    def tick():
        state["n"] += 1
        return f"2026-10-30T10:00:{state['n']:02d}Z"

    monkeypatch.setattr("app.db.utc_now_iso", tick)


def test_open_to_in_progress(client, make_request):
    """open -> in_progress succeeds and does not set resolved_at."""
    created = make_request()
    response = patch_status(client, created["id"], "in_progress")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["resolved_at"] is None
    assert body["created_at"] == created["created_at"]


def test_in_progress_to_resolved_sets_resolved_at(client, make_request):
    """Resolving sets resolved_at to a UTC timestamp in the same format as created_at."""
    created = make_request()
    patch_status(client, created["id"], "in_progress")
    response = patch_status(client, created["id"], "resolved")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert UTC_TS.match(body["resolved_at"])
    assert body["resolved_at"] >= body["created_at"]
    assert client.get(f"/requests/{created['id']}").json() == body


def test_status_change_does_not_touch_other_fields(client, make_request):
    """Only status (and resolved_at) change on a transition."""
    created = make_request(description="Pipe burst in basement")
    updated = patch_status(client, created["id"], "in_progress").json()
    for field in ("id", "title", "description", "category", "priority", "due_date", "created_at"):
        assert updated[field] == created[field]


@pytest.mark.parametrize(
    "path, bad_target",
    [
        ([], "resolved"),                              # open -> resolved
        (["in_progress"], "open"),                     # in_progress -> open
        (["in_progress", "resolved"], "in_progress"),  # resolved -> in_progress
        (["in_progress", "resolved"], "open"),         # resolved -> open (reopen)
    ],
    ids=["open_to_resolved", "in_progress_to_open", "resolved_to_in_progress", "resolved_to_open"],
)
def test_invalid_transition_returns_409_and_leaves_record_unchanged(client, make_request, path, bad_target):
    """Skipping, backward and reopen transitions return 409 and do not modify the record."""
    created = make_request()
    for step in path:
        assert patch_status(client, created["id"], step).status_code == 200
    before = client.get(f"/requests/{created['id']}").json()
    response = patch_status(client, created["id"], bad_target)
    assert_error_shape(response, 409, "invalid_transition")
    assert client.get(f"/requests/{created['id']}").json() == before


@pytest.mark.parametrize("path", [[], ["in_progress"], ["in_progress", "resolved"]], ids=["open", "in_progress", "resolved"])
def test_repeating_current_status_succeeds_without_changing_timestamps(client, make_request, fake_clock, path):
    """Re-sending the current status returns 200 and leaves the record (incl. resolved_at) untouched."""
    created = make_request()
    for step in path:
        patch_status(client, created["id"], step)
    before = client.get(f"/requests/{created['id']}").json()
    current = before["status"]
    response = patch_status(client, created["id"], current)
    assert response.status_code == 200
    assert response.json() == before
    assert client.get(f"/requests/{created['id']}").json() == before


def test_patch_missing_request_returns_404(client):
    """PATCH on an unknown id returns 404 with the error JSON."""
    assert_error_shape(patch_status(client, 4242, "in_progress"), 404, "not_found")


def test_patch_non_integer_id_returns_422(client):
    """PATCH with a non-numeric id is a validation error."""
    assert_error_shape(patch_status(client, "abc", "in_progress"), 422, "validation_error")


@pytest.mark.parametrize(
    "body",
    [
        {"status": "closed"},
        {"status": "IN_PROGRESS"},
        {"status": None},
        {"status": 1},
        {},
        {"status": "in_progress", "resolved_at": "2026-10-30T00:00:00Z"},
        {"status": "in_progress", "priority": "low"},
        ["in_progress"],
    ],
    ids=["bad_value", "wrong_case", "null", "number", "missing", "extra_resolved_at", "extra_priority", "not_object"],
)
def test_patch_rejects_invalid_body_and_leaves_record_unchanged(client, make_request, body):
    """Invalid PATCH bodies return 422 and do not partially update the record."""
    created = make_request()
    before = client.get(f"/requests/{created['id']}").json()
    assert_error_shape(client.patch(f"/requests/{created['id']}/status", json=body), 422, "validation_error")
    assert client.get(f"/requests/{created['id']}").json() == before


def test_patch_rejects_malformed_json(client, make_request):
    """Unparseable PATCH body returns 422 and leaves the record alone."""
    created = make_request()
    response = client.patch(
        f"/requests/{created['id']}/status", content="{status:", headers={"Content-Type": "application/json"}
    )
    assert_error_shape(response, 422, "validation_error")
    assert client.get(f"/requests/{created['id']}").json()["status"] == "open"
