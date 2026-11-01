"""Tests for GET /reports/summary."""
from datetime import datetime, timedelta, timezone

import pytest

STATUSES = ["open", "in_progress", "resolved"]
PRIORITIES = ["low", "medium", "high"]


def summary(client, **params):
    return client.get("/reports/summary", params=params)


def move(client, request_id, *statuses):
    for status in statuses:
        assert client.patch(f"/requests/{request_id}/status", json={"status": status}).status_code == 200


def test_demo_scenario_summary(client, make_request):
    """The three-request demo scenario produces the documented summary for as_of=2026-10-31."""
    water = make_request(title="Water leakage", category="plumbing", priority="high", due_date="2026-10-29", description="")
    gate = make_request(title="Gate light failure", category="electrical", priority="medium", due_date="2026-10-30", description="")
    make_request(title="Camera check", category="security", priority="low", due_date="2026-11-02", description="")
    move(client, water["id"], "in_progress")
    move(client, gate["id"], "in_progress", "resolved")

    response = summary(client, as_of="2026-10-31")
    assert response.status_code == 200
    assert response.json() == {
        "as_of": "2026-10-31",
        "total": 3,
        "status_counts": {"open": 1, "in_progress": 1, "resolved": 1},
        "priority_counts": {"low": 1, "medium": 1, "high": 1},
        "overdue_count": 1,
    }


def test_overdue_boundary_due_equal_to_as_of_is_not_overdue(client, make_request):
    """A request due on the as_of date itself is not overdue."""
    make_request(due_date="2026-10-31")
    assert summary(client, as_of="2026-10-31").json()["overdue_count"] == 0


def test_overdue_boundary_due_day_before_as_of_is_overdue(client, make_request):
    """A request due one day before as_of is overdue."""
    make_request(due_date="2026-10-30")
    assert summary(client, as_of="2026-10-31").json()["overdue_count"] == 1


def test_overdue_boundary_due_day_after_as_of_is_not_overdue(client, make_request):
    """A request due after as_of is not overdue."""
    make_request(due_date="2026-11-01")
    assert summary(client, as_of="2026-10-31").json()["overdue_count"] == 0


def test_resolved_requests_are_never_overdue(client, make_request):
    """Resolved requests with a past due date are excluded from overdue_count."""
    done = make_request(due_date="2020-01-01")
    move(client, done["id"], "in_progress", "resolved")
    assert summary(client, as_of="2026-10-31").json()["overdue_count"] == 0


def test_in_progress_and_open_past_due_are_overdue(client, make_request):
    """Both open and in_progress requests count as overdue once past due."""
    make_request(due_date="2026-10-01")
    started = make_request(due_date="2026-10-02")
    move(client, started["id"], "in_progress")
    assert summary(client, as_of="2026-10-31").json()["overdue_count"] == 2


def test_overdue_depends_on_as_of(client, make_request):
    """The same data is overdue or not depending on the as_of date requested."""
    make_request(due_date="2026-10-15")
    assert summary(client, as_of="2026-10-15").json()["overdue_count"] == 0
    assert summary(client, as_of="2026-10-16").json()["overdue_count"] == 1
    assert summary(client, as_of="2026-01-01").json()["overdue_count"] == 0


def test_empty_database_has_all_zero_counts(client):
    """With no data every status and priority is present with a zero count."""
    body = summary(client, as_of="2026-10-31").json()
    assert body["total"] == 0
    assert body["overdue_count"] == 0
    assert body["status_counts"] == dict.fromkeys(STATUSES, 0)
    assert body["priority_counts"] == dict.fromkeys(PRIORITIES, 0)


def test_unused_statuses_and_priorities_are_zero_filled(client, make_request):
    """Statuses/priorities with no records still appear with 0 alongside non-zero ones."""
    make_request(priority="high")
    body = summary(client, as_of="2026-10-31").json()
    assert body["status_counts"] == {"open": 1, "in_progress": 0, "resolved": 0}
    assert body["priority_counts"] == {"low": 0, "medium": 0, "high": 1}


def test_total_matches_sum_of_counts(client, make_request):
    """total equals both the status and priority count sums."""
    for priority in ["low", "low", "medium", "high", "high", "high"]:
        make_request(priority=priority)
    body = summary(client, as_of="2026-10-31").json()
    assert body["total"] == 6
    assert sum(body["status_counts"].values()) == 6
    assert sum(body["priority_counts"].values()) == 6


def test_as_of_omitted_defaults_to_current_utc_date(client, make_request):
    """Without as_of the report uses today's UTC date for the overdue calculation."""
    make_request(due_date="2000-01-01")
    make_request(due_date="2999-12-31")
    before = datetime.now(timezone.utc).date()
    body = summary(client).json()
    after = datetime.now(timezone.utc).date()
    assert body["as_of"] in {before.isoformat(), after.isoformat()}
    assert body["overdue_count"] == 1


def test_as_of_omitted_due_today_is_not_overdue_and_yesterday_is(client, make_request):
    """Relative to the default as_of, due today is on time and due yesterday is overdue."""
    today = datetime.now(timezone.utc).date()
    make_request(due_date=today.isoformat())
    make_request(due_date=(today - timedelta(days=1)).isoformat())
    body = summary(client).json()
    if body["as_of"] == today.isoformat():  # guard against running across UTC midnight
        assert body["overdue_count"] == 1


@pytest.mark.parametrize("as_of", ["2026-02-30", "abc", "2026/10/31", "20261031", "2026-10-31T00:00:00", "31-10-2026", ""])
def test_invalid_as_of_returns_422(client, as_of):
    """Malformed or impossible as_of dates return a 422 error JSON."""
    response = summary(client, as_of=as_of)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert error["details"][0]["field"] == "as_of"
