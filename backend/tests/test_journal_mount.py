from fastapi.testclient import TestClient

from app.main import app
from trading_ms.main import FRONTEND_DIST


def test_journal_is_available_beside_main_api():
    client = TestClient(app)

    assert client.get('/health').status_code == 200
    assert client.get('/journal-app/api/trades').status_code == 200
    assert client.get('/journal-app/api/settings').status_code == 200
    if FRONTEND_DIST.exists():
        assert client.get('/journal-app/').status_code == 200


def test_journal_cannot_shut_down_unified_process():
    client = TestClient(app)

    assert client.post('/journal-app/api/system/shutdown').status_code in {404, 405}
