"""Persistence tests: data lives in the SQLite file, not in the app instance."""
from fastapi.testclient import TestClient

from app.main import create_app

PAYLOAD = {
    "title": "Water leakage",
    "description": "Basement pipe",
    "category": "plumbing",
    "priority": "high",
    "due_date": "2026-10-29",
}


def new_client(db_path):
    """Build a brand-new app (and TestClient) on the given database file."""
    return TestClient(create_app(db_path))


def test_data_survives_application_restart(db_path):
    """Records, statuses and resolved_at are still there after building a new app on the same file."""
    first = new_client(db_path)
    open_rec = first.post("/requests", json={**PAYLOAD, "title": "Camera check"}).json()
    started = first.post("/requests", json={**PAYLOAD, "title": "Gate light failure"}).json()
    done = first.post("/requests", json={**PAYLOAD, "title": "Lift stuck"}).json()
    first.patch(f"/requests/{started['id']}/status", json={"status": "in_progress"})
    first.patch(f"/requests/{done['id']}/status", json={"status": "in_progress"})
    resolved = first.patch(f"/requests/{done['id']}/status", json={"status": "resolved"}).json()
    before = first.get("/requests").json()
    first.close()

    second = new_client(db_path)
    after = second.get("/requests").json()
    assert after == before
    assert after["total"] == 3
    assert second.get(f"/requests/{open_rec['id']}").json()["status"] == "open"
    assert second.get(f"/requests/{started['id']}").json()["status"] == "in_progress"
    restored = second.get(f"/requests/{done['id']}").json()
    assert restored["status"] == "resolved"
    assert restored["resolved_at"] == resolved["resolved_at"] and restored["resolved_at"] is not None


def test_new_instance_continues_working_with_existing_data(db_path):
    """After a restart new records get fresh ids and old ones can still be transitioned."""
    first = new_client(db_path)
    old = first.post("/requests", json=PAYLOAD).json()

    second = new_client(db_path)
    new = second.post("/requests", json={**PAYLOAD, "title": "Second request"}).json()
    assert new["id"] > old["id"]
    assert second.patch(f"/requests/{old['id']}/status", json={"status": "in_progress"}).status_code == 200
    assert second.get("/requests").json()["total"] == 2


def test_fresh_database_path_starts_empty(tmp_path):
    """A different database file contains none of another file's data."""
    one = new_client(tmp_path / "one.db")
    one.post("/requests", json=PAYLOAD)
    two = new_client(tmp_path / "two.db")
    body = two.get("/requests").json()
    assert body["total"] == 0 and body["items"] == []
    assert one.get("/requests").json()["total"] == 1


def test_fixture_database_starts_empty(client):
    """The per-test client fixture starts with no records."""
    assert client.get("/requests").json()["total"] == 0


def test_create_app_is_idempotent_on_existing_database(db_path):
    """Calling create_app repeatedly on the same file (init_db) keeps existing data."""
    first = new_client(db_path)
    created = first.post("/requests", json=PAYLOAD).json()
    for _ in range(2):
        again = new_client(db_path)
        body = again.get("/requests").json()
        assert body["total"] == 1
        assert body["items"][0] == created


def test_create_app_creates_missing_parent_directory(tmp_path):
    """init_db creates the database directory if it does not exist yet."""
    nested = tmp_path / "nested" / "dir" / "requests.db"
    client = new_client(nested)
    assert client.post("/requests", json=PAYLOAD).status_code == 201
    assert nested.exists()


def test_two_apps_share_one_database_file(db_path):
    """Two live app instances on the same file see each other's writes."""
    a, b = new_client(db_path), new_client(db_path)
    created = a.post("/requests", json=PAYLOAD).json()
    assert b.get(f"/requests/{created['id']}").json() == created
    b.patch(f"/requests/{created['id']}/status", json={"status": "in_progress"})
    assert a.get(f"/requests/{created['id']}").json()["status"] == "in_progress"
