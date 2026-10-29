import os

os.environ.setdefault("JWT_SECRET", "test-secret-not-real")
os.environ.setdefault("DEMO_PASSWORD", "test-pass")
os.environ.setdefault("VOICE_WEBHOOK_SECRET", "voice-test-secret")

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Agent
from app.llm import MockLLM


@pytest.fixture
def agent():
    return Agent(llm=MockLLM())


@pytest.fixture
def client():
    main.agent = Agent(llm=MockLLM())
    return TestClient(main.app)


def token(client, user):
    r = client.post("/token", data={"username": user, "password": "test-pass"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return lambda user: token(client, user)
