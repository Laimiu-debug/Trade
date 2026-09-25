from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEST_STATE_ROOT = ROOT / ".test-state"
TEST_STATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TDX_TREND_APP_STATE_PATH", str(TEST_STATE_ROOT / "app_state.json"))
os.environ.setdefault("TDX_TREND_SIM_STATE_PATH", str(TEST_STATE_ROOT / "sim_state.json"))

from app.main import app

client = TestClient(app)


def test_ai_playbook_endpoint() -> None:
    resp = client.get("/api/ai/playbook")
    assert resp.status_code == 200
    body = resp.json()
    assert "playbook" in body and isinstance(body["playbook"], dict)
    assert "text" in body and body["text"]
    assert "wyckoff_trend_v1" in body["playbook"]["strategies"]


def test_ai_local_playbook_roundtrip() -> None:
    put_resp = client.put(
        "/api/ai/playbook/local",
        json={"principles": ["只做主板", "止损不超过5%"]},
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["principles"] == ["只做主板", "止损不超过5%"]
    assert body["updated_at"]

    get_resp = client.get("/api/ai/playbook/local")
    assert get_resp.status_code == 200
    assert get_resp.json()["principles"] == ["只做主板", "止损不超过5%"]


def test_ai_quick_prompts_roundtrip() -> None:
    payload = {
        "templates": [
            {
                "id": "t1",
                "page": "chart",
                "label": "解释起爆日",
                "prompt": "为什么判定这个起爆日？",
                "pinned": True,
            }
        ]
    }
    put_resp = client.put("/api/ai/quick-prompts", json=payload)
    assert put_resp.status_code == 200
    get_resp = client.get("/api/ai/quick-prompts")
    assert get_resp.status_code == 200
    templates = get_resp.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["label"] == "解释起爆日"


def test_ai_chat_endpoint_without_key() -> None:
    resp = client.post(
        "/api/ai/chat",
        json={
            "messages": [{"role": "user", "content": "这只票趋势如何？"}],
            "context": {
                "page": "chart",
                "title": "K线 sz300750",
                "symbol": "sz300750",
            },
            "stream": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"]
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"]


def test_ai_chat_stream_endpoint_without_key() -> None:
    resp = client.post(
        "/api/ai/chat",
        json={
            "messages": [{"role": "user", "content": "解释回测"}],
            "context": {"page": "backtest", "title": "回测"},
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "data:" in resp.text


def test_ai_sessions_and_usage_endpoints() -> None:
    sessions = client.get("/api/ai/sessions")
    assert sessions.status_code == 200
    assert "items" in sessions.json()
    usage = client.get("/api/ai/usage")
    assert usage.status_code == 200
    assert usage.json()["total_tokens"] >= 0


def test_ai_parameter_proposal_endpoint() -> None:
    resp = client.post(
        "/api/ai/parameter-proposals",
        json={
            "message": "收紧止损",
            "context": {"page": "backtest", "title": "回测"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"]
    assert "changes" in body
