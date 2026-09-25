"""AI 参数提案 apply 与白名单回归测试。

修复3:page_form/strategy_params 不再假装成功,标记 pending_frontend=True。
修复4:app_config 改用白名单(APPROVED_APP_CONFIG_FIELDS),黑名单字段(tdx_data_path 等)被拒。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.core.ai_parameter_proposal import AIParameterProposalService
from app.core.strategy_registry import StrategyRegistry
from app.models import AIChatContext


class FakeConfig:
    """模拟 AppConfig:支持 model_dump / model_validate,字段可任意设置。"""

    def __init__(self) -> None:
        self.tdx_data_path = "C:/data"
        self.api_key = ""
        self.api_key_path = ""
        self.top_n = 30
        self.initial_capital = 1_000_000.0
        self.return_window_days = 20
        self.candles_window_bars = 120
        self.turnover_threshold = 0.5
        self.amount_threshold = 1e8
        self.amplitude_threshold = 0.5
        self.backtest_plateau_workers = 4

    def model_dump(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def model_validate(cls, data: dict) -> "FakeConfig":
        obj = cls()
        obj.__dict__.update(data)
        return obj


def _build_service() -> tuple[AIParameterProposalService, FakeConfig]:
    """构造一个带可变 FakeConfig 的 proposal service。"""
    config = FakeConfig()
    registry = StrategyRegistry()
    service = AIParameterProposalService(
        registry,
        resolve_provider=lambda: None,  # 无 provider,走 heuristic
        resolve_api_key=lambda p: "",
        enrich_context=lambda ctx: ctx,
        load_user_principles=lambda: [],
        timeout_sec=lambda: 10.0,
        get_config=lambda: config,
        set_config=lambda c: None,  # 测试不依赖 set_config 副作用
    )
    return service, config


def _inject_proposal(
    service: AIParameterProposalService,
    changes: list[dict],
) -> str:
    """直接往 _proposals 注入一个 proposal,返回 proposal_id。"""
    from datetime import datetime, timedelta

    proposal_id = "pp_test_001"
    with service._lock:
        service._proposals[proposal_id] = {
            "proposal_id": proposal_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": (datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds"),
            "context_page": "backtest",
            "summary": "test",
            "changes": changes,
        }
    return proposal_id


def test_page_form_change_marked_pending_frontend() -> None:
    """修复3:page_form change apply 后标记 pending_frontend=True(而非假装已写入)。"""
    service, _ = _build_service()
    change = {
        "change_id": "c1",
        "scope": "page_form",
        "target": "backtest.stop_loss",
        "new_value": 0.04,
        "old_value": 0.05,
        "reason": "收紧止损",
        "risk_level": "low",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c1"])
    assert len(result["applied"]) == 1
    applied = result["applied"][0]
    assert applied.get("pending_frontend") is True, "page_form 应标记 pending_frontend"
    assert len(result["rejected"]) == 0


def test_strategy_params_change_marked_pending_frontend() -> None:
    """修复3:strategy_params change 同样标记 pending_frontend=True。"""
    service, _ = _build_service()
    change = {
        "change_id": "c2",
        "scope": "strategy_params",
        "target": "strategy.wyckoff_trend_v1.health_weight",
        "new_value": 0.5,
        "old_value": 0.45,
        "reason": "调整权重",
        "risk_level": "low",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c2"])
    assert len(result["applied"]) == 1
    assert result["applied"][0].get("pending_frontend") is True


def test_app_config_change_not_marked_pending() -> None:
    """修复3:app_config change 真正写回配置,不带 pending_frontend 标记。"""
    service, config = _build_service()
    change = {
        "change_id": "c3",
        "scope": "app_config",
        "target": "app_config.top_n",
        "new_value": 50,
        "old_value": 30,
        "reason": "扩大选股池",
        "risk_level": "low",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c3"])
    assert len(result["applied"]) == 1
    assert "pending_frontend" not in result["applied"][0], "app_config 不应有 pending_frontend"


def test_app_config_whitelist_rejects_sensitive_field() -> None:
    """修复4:AI 不能修改 tdx_data_path 等非白名单字段(即使不在 BLOCKLIST 内)。"""
    service, _ = _build_service()
    change = {
        "change_id": "c4",
        "scope": "app_config",
        "target": "app_config.tdx_data_path",
        "new_value": "C:/malicious/path",
        "old_value": "C:/original",
        "reason": "改路径",
        "risk_level": "high",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c4"], confirm_high_risk=True)
    assert len(result["rejected"]) == 1, "tdx_data_path 不在白名单应被拒"
    assert "白名单" in result["rejected"][0]["reason"] or "不可修改" in result["rejected"][0]["reason"]


def test_app_config_whitelist_rejects_api_key() -> None:
    """修复4:api_key 等敏感字段即使在白名单也拒绝(BLOCKLIST 兜底)。"""
    service, _ = _build_service()
    change = {
        "change_id": "c5",
        "scope": "app_config",
        "target": "app_config.api_key",
        "new_value": "sk-leaked",
        "old_value": "",
        "reason": "改 key",
        "risk_level": "high",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c5"], confirm_high_risk=True)
    assert len(result["rejected"]) == 1


def test_app_config_whitelist_rejects_out_of_range_value() -> None:
    """修复4:白名单内字段也要校验范围(top_n 上限 500)。"""
    service, _ = _build_service()
    change = {
        "change_id": "c6",
        "scope": "app_config",
        "target": "app_config.top_n",
        "new_value": 99999,  # 超过 500 上限
        "old_value": 30,
        "reason": "放大选股池",
        "risk_level": "low",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c6"])
    assert len(result["rejected"]) == 1, "超范围值应被拒"
    assert "上限" in result["rejected"][0]["reason"]


def test_app_config_whitelist_allows_valid_change() -> None:
    """修复4:白名单内 + 范围内的字段应正常 apply。"""
    service, config = _build_service()
    change = {
        "change_id": "c7",
        "scope": "app_config",
        "target": "app_config.initial_capital",
        "new_value": 2000000.0,
        "old_value": 1000000.0,
        "reason": "调高初始资金",
        "risk_level": "low",
        "validation_error": None,
    }
    pid = _inject_proposal(service, [change])
    result = service.apply_proposal(pid, change_ids=["c7"])
    assert len(result["applied"]) == 1
    assert len(result["rejected"]) == 0
