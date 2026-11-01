"""Shared fixtures. Every test gets its own throw-away SQLite file."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

VALID_PAYLOAD = {
    "title": "Water leakage",
    "description": "",
    "category": "plumbing",
    "priority": "high",
    "due_date": "2026-10-29",
}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_requests.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture
def make_request(client):
    """Create a request through the API, overriding any default field."""

    def _make(**overrides):
        response = client.post("/requests", json={**VALID_PAYLOAD, **overrides})
        assert response.status_code == 201, response.text
        return response.json()

    return _make
