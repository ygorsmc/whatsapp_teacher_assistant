import pytest
from fastapi.testclient import TestClient

# We mock dependencies before importing the app to avoid loading heavy modules or DB
@pytest.fixture
def client(mocker):
    # Mock settings to avoid loading environment variables that might fail in CI
    mocker.patch("src.shared.config.Settings")
    # Mock telemetry and DB
    mocker.patch("src.shared.telemetry.setup_telemetry")
    mocker.patch("src.shared.telemetry.track_event")
    mocker.patch("src.db.database.init_db")
    
    # Now import the app safely
    from src.webhook.main import app
    
    return TestClient(app)

def test_health_check(client):
    """
    Test the /health endpoint to ensure the application starts correctly.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
