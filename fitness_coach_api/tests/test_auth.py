
import pytest

from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies.database import get_db
from app.api.v1.endpoints import auth as auth_endpoint


# Fake database dependency for our tests
@pytest.fixture
def client():
    original_overrides = app.dependency_overrides.copy()

    app.dependency_overrides[get_db] = lambda: None

    yield TestClient(app)

    app.dependency_overrides.clear()
    app.dependency_overrides.update(original_overrides)


# Test successful login
def test_login_success(client, monkeypatch):

    fake_user = SimpleNamespace(
        id="user123",
        password_hash=b"fake-hash"
    )

    async def fake_get_user_by_email(db, email):
        return fake_user

    def fake_verify_password(password, hashed_password):
        return True

    def fake_create_access_token(user_id):
        return "test-access-token"

    monkeypatch.setattr(
        auth_endpoint,
        "get_user_by_email",
        fake_get_user_by_email
    )

    monkeypatch.setattr(
        auth_endpoint,
        "verify_password",
        fake_verify_password
    )

    monkeypatch.setattr(
        auth_endpoint,
        "create_access_token",
        fake_create_access_token
    )

    response = client.post(
        "/api/v1/login",
        json={
            "email": "test@example.com",
            "password": "correct-password"
        }
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["access_token"] == "test-access-token"


# Test login with an unknown email
def test_login_unknown_email(client, monkeypatch):

    async def fake_get_user_by_email(db, email):
        return None

    monkeypatch.setattr(
        auth_endpoint,
        "get_user_by_email",
        fake_get_user_by_email
    )

    response = client.post(
        "/api/v1/login",
        json={
            "email": "unknown@example.com",
            "password": "some-password"
        }
    )

    assert response.status_code == 401
    assert response.json()["success"] is False


# Test login with an incorrect password
def test_login_wrong_password(client, monkeypatch):

    fake_user = SimpleNamespace(
        id="user123",
        password_hash=b"fake-hash"
    )

    async def fake_get_user_by_email(db, email):
        return fake_user

    def fake_verify_password(password, hashed_password):
        return False

    monkeypatch.setattr(
        auth_endpoint,
        "get_user_by_email",
        fake_get_user_by_email
    )

    monkeypatch.setattr(
        auth_endpoint,
        "verify_password",
        fake_verify_password
    )

    response = client.post(
        "/api/v1/login",
        json={
            "email": "test@example.com",
            "password": "wrong-password"
        }
    )

    assert response.status_code == 401
    assert response.json()["success"] is False
