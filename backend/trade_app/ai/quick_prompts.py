"""Versioned, directory-wide quick questions. Selecting one never starts a call."""
import json
import re

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from trade_app.ai.config import encode
from trade_app.platform.db import Base
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now

PAGES = ('generic', 'market', 'research', 'backtests', 'portfolio', 'valuation', 'reviews')
SYSTEM = [
    {'id': 'system:evidence', 'page': 'generic', 'label': '证据与缺失', 'prompt': '检查所选资料中的证据、风险与缺失信息；区分事实、推断和假设，注明来源ID与日期。', 'pinned': True, 'readonly': True},
    {'id': 'system:valuation', 'page': 'valuation', 'label': '估值假设', 'prompt': '逐项审查所选估值情景的盈利、增长、PE和情绪假设，说明哪些结论无法由现有资料验证。', 'pinned': False, 'readonly': True},
    {'id': 'system:backtest', 'page': 'backtests', 'label': '回测边界', 'prompt': '核对所选回测的执行时序、费用、样本选择、回撤和局限；不要把回测收益作为未来收益承诺。', 'pinned': False, 'readonly': True},
    {'id': 'system:review', 'page': 'reviews', 'label': '复盘建议', 'prompt': '复盘所选账户的决策与执行，保留人工事实，给出待人工确认的改进建议。', 'pinned': False, 'readonly': True},
]


class QuickPromptList(Base):
    __tablename__ = 'ai_quick_prompt_lists'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    items_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


def get_prompts(session):
    row = session.get(QuickPromptList, 'global')
    return {'revision': row.revision if row else 0, 'items': json.loads(row.items_json) if row else [],
            'system_items': SYSTEM, 'pages': list(PAGES), 'scope': 'global_data_directory',
            'updated_at': row.updated_at if row else None}


def normalize(items):
    if not isinstance(items, list) or len(items) > 100:
        raise TradeError('INVALID_QUICK_PROMPTS', '最多保存100条快捷提问')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'id', 'page', 'label', 'prompt', 'pinned'}:
            raise TradeError('INVALID_QUICK_PROMPTS', '快捷提问字段须为id/page/label/prompt/pinned')
        identity = item['id']
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity) or identity in seen:
            raise TradeError('INVALID_QUICK_PROMPTS', '自定义提问ID无效或重复；系统模板须先复制')
        if item['page'] not in PAGES or type(item['pinned']) is not bool:
            raise TradeError('INVALID_QUICK_PROMPTS', '页面或置顶字段无效')
        for key, maximum in (('label', 80), ('prompt', 12000)):
            if not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > maximum:
                raise TradeError('INVALID_QUICK_PROMPTS', f'{key} 须为1至{maximum}字文本')
        result.append({**item, 'label': item['label'].strip(), 'prompt': item['prompt'].strip()})
        seen.add(identity)
    if len(encode(result).encode('utf-8')) > 256000:
        raise TradeError('QUICK_PROMPTS_TOO_LARGE', '快捷提问总内容不得超过256KB')
    return result


def save_prompts(session, expected_revision, items):
    normalized = normalize(items)
    before = get_prompts(session)
    if type(expected_revision) is not int or expected_revision != before['revision']:
        raise TradeError('QUICK_PROMPTS_CONFLICT', '快捷提问已被其他页面修改，当前草稿已保留，请比较后重试', 409)
    row = session.get(QuickPromptList, 'global')
    if row is None:
        row = QuickPromptList(id='global', revision=0, items_json='[]', updated_at=utc_now())
        session.add(row)
    row.revision += 1
    row.items_json, row.updated_at = encode(normalized), utc_now()
    session.flush()
    after = get_prompts(session)
    session.add(AuditEvent(id=new_id(), account_id=None, entity_type='ai_quick_prompts', entity_id='global', operation='update',
                           before_json=encode(before), after_json=encode(after), created_at=utc_now()))
    return after


def history(session):
    return [{'id': row.id, 'created_at': row.created_at, 'before': json.loads(row.before_json), 'after': json.loads(row.after_json)}
            for row in session.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'ai_quick_prompts')
                                      .order_by(AuditEvent.created_at.desc()).limit(100))]
