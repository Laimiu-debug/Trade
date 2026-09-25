"""AI 流式对话 finally 落盘回归测试。

修复前:chat_stream 的 _finalize 写在 for 循环之后(非 finally),
客户端中途断开(GeneratorExit)时 _finalize 不执行 → session 不落盘、usage 漏报。

修复后:_finalize 移入 finally,即使流式中途断开,已接收的 delta 仍落盘。
同时验证并发锁(修复5)保护 _sessions/_usage_totals。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.ai_chat_service import AIChatService
from app.core.strategy_registry import StrategyRegistry
from app.models import AIChatContext, AIChatMessage, AIChatRequest, AIProviderConfig


def _build_service(tmp_path: Path) -> AIChatService:
    """构造一个带 fake provider 的 AIChatService。"""
    provider = AIProviderConfig(
        id="fake",
        label="fake",
        base_url="http://fake.local/v1",
        model="fake-model",
        api_key="fake-key",
        api_key_path="",
        enabled=True,
    )
    registry = StrategyRegistry()
    return AIChatService(
        registry,
        resolve_provider=lambda: provider,
        resolve_api_key=lambda p: "fake-key" if p else "",
        enrich_context=lambda ctx: ctx,
        load_user_principles=lambda: [],
        timeout_sec=lambda: 10.0,
        retry_count=lambda: 0,
    )


def _request() -> AIChatRequest:
    return AIChatRequest(
        messages=[AIChatMessage(role="user", content="你好")],
        context=AIChatContext(page="generic", title="测试"),
        stream=True,
    )


def test_stream_normal_completion_finalizes_session(tmp_path: Path) -> None:
    """正常完成的流式对话应落盘 session 和 usage。"""
    service = _build_service(tmp_path)

    def fake_stream(provider, api_key, messages):
        yield {"type": "delta", "content": "你好"}
        yield {"type": "delta", "content": "，世界"}
        yield {"type": "usage", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    service._call_provider_stream = fake_stream  # type: ignore[method-assign]

    events = list(service.chat_stream(_request()))
    # 应有 delta + done 事件
    assert any("delta" in e for e in events)
    assert any("done" in e for e in events)

    # session 应已落盘(内存缓存)
    sessions = service.list_sessions(limit=5)
    assert sessions, "session 应已创建"
    # usage 应已累加
    totals = service.get_usage_totals()
    assert totals.get("total_tokens", 0) >= 15, f"usage 应累加,实际 {totals}"


def test_stream_mid_disconnect_still_finalizes(tmp_path: Path) -> None:
    """流式中途断开(GeneratorExit)时,已接收的 delta 仍应落盘。

    模拟:生成器只 yield 了部分 delta 就被消费方 close()。
    修复前:_finalize 不执行,session 丢失;修复后:finally 兜底落盘。
    """
    service = _build_service(tmp_path)

    def fake_stream(provider, api_key, messages):
        yield {"type": "delta", "content": "部分"}
        yield {"type": "delta", "content": "回复"}
        # 模拟中途断开:不再 yield(消费方会 close 生成器)

    service._call_provider_stream = fake_stream  # type: ignore[method-assign]

    # 消费前几个事件后提前关闭生成器(模拟客户端断开)
    gen = service.chat_stream(_request())
    consumed = []
    for event in gen:
        consumed.append(event)
        if "部分" in event:
            break  # 提前停止,模拟客户端断开
    gen.close()  # 触发 GeneratorExit

    # 关键断言:即使中途断开,session 也应已落盘(修复后)
    sessions = service.list_sessions(limit=5)
    assert sessions, "中途断开后 session 仍应落盘(finally 兜底)"
    # assistant 消息应包含已接收的部分内容
    session = service.get_session(sessions[0]["session_id"])
    contents = [msg.content for msg in session if msg.role == "assistant"]
    assert contents, "应有 assistant 消息"
    assert "部分" in contents[0], f"assistant 应包含已接收内容,实际 {contents[0]}"


def test_stream_empty_reply_finalizes_with_placeholder(tmp_path: Path) -> None:
    """流式返回空内容时,finally 应用占位文本落盘(不丢消息)。"""
    service = _build_service(tmp_path)

    def fake_stream(provider, api_key, messages):
        yield {"type": "delta", "content": ""}
        return  # 空回复

    service._call_provider_stream = fake_stream  # type: ignore[method-assign]

    gen = service.chat_stream(_request())
    list(gen)  # 消费完
    # 空回复也应落盘,用占位文本
    sessions = service.list_sessions(limit=5)
    assert sessions
    session = service.get_session(sessions[0]["session_id"])
    assistant_msgs = [msg for msg in session if msg.role == "assistant"]
    assert assistant_msgs, "空回复也应有 assistant 占位消息"


def test_concurrent_sessions_thread_safe(tmp_path: Path) -> None:
    """并发对话不应破坏 _sessions / _usage_totals(修复5:RLock 保护)。

    用 usage 增量验证:8 个并发对话各 +2 tokens,总增量应为 16。
    不依赖绝对值(磁盘可能已有历史 usage)。
    """
    import threading

    service = _build_service(tmp_path)

    def fake_stream(provider, api_key, messages):
        yield {"type": "delta", "content": "ok"}
        yield {"type": "usage", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    service._call_provider_stream = fake_stream  # type: ignore[method-assign]

    before = service.get_usage_totals().get("total_tokens", 0)

    errors: list[Exception] = []

    def run_one(idx: int) -> None:
        try:
            req = AIChatRequest(
                messages=[AIChatMessage(role="user", content=f"问题{idx}")],
                context=AIChatContext(page="generic", title="测试"),
                stream=True,
            )
            list(service.chat_stream(req))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_one, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发对话不应报错: {errors}"
    # usage 增量应为 16(8 个对话 × 2 tokens)
    after = service.get_usage_totals().get("total_tokens", 0)
    assert after - before == 16, f"并发 usage 增量应为 16,实际 {after - before}"
