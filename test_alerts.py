from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app


ALERT_DATA = {
    "title": "High CPU usage",
    "severity": "critical",
    "source": "server-01",
    "metric_name": "cpu_usage",
    "metric_value": 95.0,
    "threshold": 80.0,
}


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_create_alert(client: TestClient) -> None:
    response = client.post("/alerts/", json=ALERT_DATA)

    assert response.status_code == 201
    alert = response.json()
    assert alert.items() >= ALERT_DATA.items()
    assert isinstance(alert["id"], int)
    assert alert["id"] > 0
    assert datetime.fromisoformat(alert["created_at"])


def test_list_alerts(client: TestClient) -> None:
    response = client.get("/alerts/")
    assert response.status_code == 200
    assert response.json() == []

    expected_alerts = []
    for severity in ("critical", "warning"):
        response = client.post("/alerts/", json={**ALERT_DATA, "severity": severity})
        assert response.status_code == 201
        expected_alerts.append(response.json())

    response = client.get("/alerts/")

    assert response.status_code == 200
    assert sorted(response.json(), key=lambda alert: alert["id"]) == expected_alerts


def test_get_alert_by_id(client: TestClient) -> None:
    response = client.post("/alerts/", json=ALERT_DATA)
    assert response.status_code == 201
    alert = response.json()

    response = client.get(f"/alerts/{alert['id']}")

    assert response.status_code == 200
    assert response.json() == alert

    response = client.get(f"/alerts/{alert['id'] + 1}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Alert not found"}


def test_explain_alert(client: TestClient) -> None:
    response = client.post("/alerts/", json=ALERT_DATA)
    assert response.status_code == 201
    alert = response.json()

    response = client.post(f"/alerts/{alert['id']}/explain")

    assert response.status_code == 200
    explanation = response.json()
    assert explanation["alert_id"] == alert["id"]
    assert alert["severity"] in explanation["explanation"]
    assert alert["metric_name"] in explanation["explanation"]
    generated_at = datetime.fromisoformat(explanation["generated_at"])
    assert generated_at.tzinfo is not None

    response = client.post(f"/alerts/{alert['id'] + 1}/explain")
    assert response.status_code == 404
    assert response.json() == {"detail": "Alert not found"}