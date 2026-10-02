"""Explicit, hash-bound promotion of selected non-ledger legacy records."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import re

from sqlalchemy import select
from trade_app.api import settings_service as settings
from trade_app.analytics.service import projection_status
from trade_app.insights.service import create_card, list_cards, _tags
from trade_app.legacy_import.reader import canonical, digest
from trade_app.legacy_import.service import _row
from trade_app.legacy_import.supplement_models import LegacySupplementBatch, LegacySupplementItem
from trade_app.market.symbols import normalize_market_symbol
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError, new_id, utc_now
from trade_app.reviews.rounds import get_round_note, save_round_note
from trade_app.trading.models import Trade

VERSION = 'explicit-legacy-supplements-v1'
FEE_FIELDS = {'commission_rate':'commission_rate', 'commission_min':'minimum_commission',
              'stamp_tax_rate':'sell_stamp_rate', 'transfer_fee_rate':'transfer_rate'}
AI_FIELDS = {'text':('ai_score_base_url','ai_score_text_model','ai_text_model'),
             'vision':('ai_ocr_base_url','ai_ocr_vision_model','ai_vision_model')}
NOTES = [
    '只有明确勾选并确认差异的项目写入；原脱敏逻辑档案和原始文件不改动。每项来源只迁入一次。',
    '灵感卡进入当前数据目录共享库；相同正文和标签复用已有卡，不覆盖正文。新记录时间为迁入时间。',
    '费用和目标仅写入此档案已关联的新实盘账户。费用不重写历史实付金额；目标新版本会触发重新统计。',
    'AI只映射已保存的地址/模型和明确环境变量名称，不复制旧key，不发送模型请求；需要单独配置运行环境。',
    '回合须逐笔ID完整匹配；同日多回合、别名合并或交易已修改无法唯一对照时阻止迁入。旧摘要不区分人工/AI来源，保留历史来源说明。',
    '旧图片只有路径文字，未提供二进制即不能迁移；本流程不读取旧路径或扫描用户目录。',
]


def _settings(payload):
    raw = payload.get('settings', [])
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        raise TradeError('LEGACY_SETTINGS_INVALID', '旧设置须为键值对象或记录数组')
    values = {}
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get('key'), str) or row['key'] in values:
            raise TradeError('LEGACY_SETTINGS_INVALID', '旧设置键无效或重复')
        values[row['key']] = row.get('value')
    return values


def _identity(value):
    if type(value) is not int or value < 1:
        raise ValueError('来源ID必须为正整数')
    return str(value)


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'文本须为1至{limit}字')
    return value.strip()


def _old_rounds(payload):
    """Reproduce only legacy membership, including its exact original code keys."""
    groups = defaultdict(list)
    for trade in sorted(payload.get('trades', []), key=lambda item: (item['trade_date'], int(item['id']))):
        groups[trade['code']].append(trade)
    result = []
    for code, trades in groups.items():
        position, current = 0, []
        for trade in trades:
            if position == 0 and trade['side'] == 'sell':
                result.append({'code':code,'start_date':trade['trade_date'],'ids':[str(trade['id'])],'valid':False})
                continue
            current.append(trade)
            position += trade['qty'] if trade['side'] == 'buy' else -trade['qty']
            if position <= 0:
                result.append({'code':code,'start_date':current[0]['trade_date'],'ids':[str(item['id']) for item in current],'valid':position == 0})
                position, current = 0, []
        if current:
            result.append({'code':code,'start_date':current[0]['trade_date'],'ids':[str(item['id']) for item in current],'valid':True})
    return result


def _round_action(session, row, payload, source, source_rounds, duplicates):
    if not row.account_id:
        raise ValueError('请先将L核心事实转换到独立新实盘账户')
    code, start = source.get('code'), source.get('start_date')
    if not isinstance(code, str):
        raise ValueError('回合证券代码须为文本')
    if not isinstance(start, str) or date.fromisoformat(start).isoformat() != start:
        raise ValueError('回合开始日期无效')
    symbol = ''.join(normalize_market_symbol(code))
    candidates = [item for item in source_rounds if item['code'] == code and item['start_date'] == start]
    if len(candidates) != 1 or not candidates[0]['valid'] or duplicates[(str(code),str(start))] != 1:
        raise ValueError('旧证券/开始日对应多个回合、重复摘要或异常成交，不能猜测关联')
    mapped = {item['source_id']:item['target_id'] for item in json.loads(row.mappings_json) if item['section'] == 'trades'}
    source_ids = candidates[0]['ids']
    if any(identity not in mapped for identity in source_ids):
        raise ValueError('旧回合交易尚未完整映射')
    target_ids = [mapped[identity] for identity in source_ids]
    for identity in target_ids:
        trade = session.get(Trade, identity)
        if trade is None or trade.account_id != row.account_id or trade.revision != 1:
            raise ValueError('已导入交易被修改或删除，不能沿用旧回合映射')
    projection = projection_status(session,row.account_id)
    if projection['status'] != 'fresh':
        raise ValueError('新账户统计尚未完成，请刷新后核对回合')
    matches = [item for item in projection['result']['rounds'] if item['symbol'] == symbol and item['start_date'] == start and item['trade_ids'] == target_ids and item['status'] != 'anomaly']
    if len(matches) != 1:
        raise ValueError('新回合完整成交集合不一致；可能存在别名合并或新增成交')
    target = get_round_note(session,row.account_id,matches[0]['id'])
    return {'kind':'round','account_id':row.account_id,'round_id':matches[0]['id'],
            'expected_revision':target['revision'],'account_revision':projection['account_input_revision'],
            'projection_version':projection['projection_version'],'source_trade_ids':source_ids,
            'target_trade_ids':target_ids,'before':target['summary'], 'after':_text(source.get('review_summary'),50000)}


def inspect(session, import_id, *, secret_refs=None):
    refs = {} if secret_refs is None else secret_refs
    if (not isinstance(refs, dict) or set(refs)-{'text','vision'} or
            any(not isinstance(value,str) or (value and not re.fullmatch(r'TRADE_AI_[A-Z0-9_]{1,80}',value)) for value in refs.values())):
        raise TradeError('INVALID_AI_SECRET_REF','仅接受 TRADE_AI_ 环境变量名称，不能填写密钥值')
    row = _row(session,import_id)
    payload = json.loads(row.archive_json)
    if digest(payload) != row.logical_sha256:
        raise TradeError('LEGACY_ARCHIVE_CORRUPT','逻辑档案摘要不匹配',409)
    applied = {item.source_key:json.loads(item.target_json) for item in session.scalars(select(LegacySupplementItem).where(LegacySupplementItem.import_id == import_id))}
    output = {'version':VERSION,'import_id':import_id,'expected_revision':row.revision,'source_sha256':row.source_sha256,
              'logical_sha256':row.logical_sha256,'account_id':row.account_id,'items':[],'unmapped':[],'notes':NOTES}
    if not row.source_kind.startswith('laimiu_'):
        output['unmapped'].append({'source':'final-trade','reason':'此批仅承接Laimiu卡片/设置/回合；final模拟/研究事实保持只读档案'})
        return output
    def add(key, label, source, factory):
        item={'key':key,'label':label,'source':source,'status':'ready','action':None,'reason':None}
        if key in applied:
            item.update(status='applied',target=applied[key])
        else:
            try: item['action']=factory()
            except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError,TradeError) as exc:
                item.update(status='blocked',reason=exc.message if isinstance(exc,TradeError) else str(exc))
        output['items'].append(item)
    cards = list_cards(session)
    raw_cards=payload.get('flash_cards',[])
    if not isinstance(raw_cards,list): raw_cards=[];output['unmapped'].append({'source':'flash_cards','reason':'不是数组'})
    counts=Counter(str(item.get('id')) for item in raw_cards if isinstance(item,dict))
    for index, source in enumerate(raw_cards):
        def card(source=source):
            identity=_identity(source.get('id'))
            if counts[identity] != 1: raise ValueError('卡片来源ID重复')
            content=_text(source.get('content'),20000)
            tags=source.get('tags','')
            if not isinstance(tags,str): raise ValueError('旧卡片tags必须为逗号分隔文本')
            tags=_tags(tags.split(','))
            existing=next((item for item in cards if item['content']==content and item['tags']==tags),None)
            return {'kind':'card','before':existing,'after':{'content':content,'tags':tags},'target_scope':'global_data_directory'}
        add('card:'+str(source.get('id',index)) if isinstance(source,dict) else 'invalid-card:'+str(index),'共享灵感卡',source,card)
    raw_rounds=payload.get('round_reviews',[])
    if not isinstance(raw_rounds,list): raw_rounds=[];output['unmapped'].append({'source':'round_reviews','reason':'不是数组'})
    try: old_rounds=_old_rounds(payload)
    except (ValueError,TypeError,KeyError): old_rounds=[]
    duplicate_rounds=Counter((str(item.get('code')),str(item.get('start_date'))) for item in raw_rounds if isinstance(item,dict))
    for index,source in enumerate(raw_rounds):
        def round_note(source=source):
            _identity(source.get('id'))
            return _round_action(session,row,payload,source,old_rounds,duplicate_rounds)
        add('round:'+str(source.get('id',index)) if isinstance(source,dict) else 'invalid-round:'+str(index),'旧回合摘要',source,round_note)
    try: old=_settings(payload)
    except TradeError as exc: old={};output['unmapped'].append({'source':'settings','reason':exc.message})
    used=set()
    def group_action(group, fields, changes, *, account=False):
        used.update(fields)
        def calculate():
            if account and not row.account_id: raise ValueError('请先转换到独立新实盘账户')
            current=settings.get_group(session,group,row.account_id if account else None)
            proposed=changes(deepcopy(current['value']))
            validated=settings.preview(session,group,row.account_id if account else None,current['revision'],proposed)
            return {'kind':'settings','group':group,'account_id':row.account_id if account else None,
                    'expected_revision':current['revision'],'before':validated['before'],'after':validated['after'],
                    'diff':validated['diff'],'target_scope':current['scope']}
        add('setting:'+group,'设置 · '+group,{key:old[key] for key in fields},calculate)
    fee_keys=set(old)&set(FEE_FIELDS)
    if fee_keys:
        def fees(value):
            for key in fee_keys: value[FEE_FIELDS[key]]=old[key]
            return value
        group_action('fees',fee_keys,fees,account=True)
    target_keys=set(old)&{'wave_pct','node_count'}
    if target_keys:
        def targets(value):
            if 'wave_pct' in old:
                percent=Decimal(str(old['wave_pct']))
                if not percent.is_finite():raise ValueError('节点涨幅不是有限数')
                value['multiplier']=format(Decimal(1)+percent/100,'f')
            if 'node_count' in old:
                count=str(old['node_count'])
                if not count.isdigit():raise ValueError('节点数量须为整数字符串')
                value['node_count']=int(count)
            return value
        group_action('targets',target_keys,targets,account=True)
    print_keys=set(old)&{'pdf_username','pdf_export_dir'}
    if print_keys:
        def printing(value):
            if 'pdf_username' in old:value['author']=old['pdf_username']
            if 'pdf_export_dir' in old:value['export_directory']=old['pdf_export_dir']
            return value
        group_action('print',print_keys,printing)
    if 'market_priority' in old:
        def market(value):
            order=old['market_priority']
            if not isinstance(order,str):raise ValueError('行情来源顺序不是文本')
            values=[item.strip() for item in order.split(',')]
            if set(values) != {'akshare','baostock'} or len(values)!=2:
                raise ValueError('旧tdx/web等来源没有等价在线优先级；保留原值并到设置页明确选择，不删除不支持来源后猜顺序')
            return {'provider':'auto','provider_order':values}
        group_action('market_sources',{'market_priority'},market)
    ai_keys=set(old)&{'ai_score_base_url','ai_score_text_model','ai_ocr_base_url','ai_ocr_vision_model','ai_base_url','ai_text_model','ai_vision_model'}
    if ai_keys:
        def models(value):
            mapped=0
            refs=secret_refs or {}
            for channel,(base_key,model_key,fallback_key) in AI_FIELDS.items():
                base=old.get(base_key) or old.get('ai_base_url') or ''
                model=old.get(model_key) or old.get(fallback_key) or ''
                # A shared legacy URL alone does not imply that both channels were configured.
                if not model and not old.get(base_key):continue
                if not base and not model:continue
                if not base or not model:raise ValueError('旧AI地址/模型不完整，不能猜测另一字段')
                if not isinstance(base,str) or not isinstance(model,str) or '[REDACTED:' in base or '[REDACTED:' in model:
                    raise ValueError('旧AI地址/模型字段无效或已脱敏，不能将脱敏标记当配置')
                value[channel]={'base_url':base,'model':model,'secret_ref':refs.get(channel,'')}
                mapped+=1
            if not mapped:raise ValueError('旧AI未配置可迁入的地址/模型')
            return value
        group_action('ai',ai_keys,models)
    for key in sorted(set(old)-used):
        output['unmapped'].append({'source':'settings/'+key,'reason':'旧credential只保留脱敏标记，不迁入' if 'key' in key.lower() or 'secret' in key.lower() else '暂无等价版本化目标；保留在只读档案，不写入全局/路径/浏览器配置'})
    daily=payload.get('daily_reviews',[])
    if isinstance(daily,list) and any(isinstance(item,dict) and item.get('images') not in (None,'','[]',[]) for item in daily):
        output['unmapped'].append({'source':'daily_reviews/images','reason':'仅有旧附件路径引用，未提供图片二进制；无法判断文件存在，不读取原路径'})
    return output


def preview(session, import_id, body):
    selected=body['selected_keys']
    if not isinstance(selected,list) or len(selected)>500 or any(not isinstance(key,str) or len(key)>128 for key in selected) or len(set(selected)) != len(selected):
        raise TradeError('LEGACY_SELECTION_INVALID','最多明确选择500项且不可重复')
    value=inspect(session,import_id,secret_refs=body.get('secret_refs'))
    if value['expected_revision'] != body['expected_revision']:
        raise TradeError('LEGACY_REVISION_CONFLICT','旧档案修订已变化，请刷新后重新预览',409)
    by_key={item['key']:item for item in value['items']}
    if len(by_key)!=len(value['items']) or any(key not in by_key for key in selected):
        raise TradeError('LEGACY_SELECTION_INVALID','存在重复来源身份或未知选择')
    value['selected_keys']=sorted(selected)
    value['selected']=[by_key[key] for key in sorted(selected)]
    value['can_apply']=bool(selected) and all(item['status']=='ready' for item in value['selected'])
    value['preview_sha256']=digest(value)
    return value


def apply(session, import_id, body):
    if not body['acknowledge_limitations']:
        raise TradeError('LEGACY_ACK_REQUIRED','请确认本批逐项差异和来源边界')
    request={key:body[key] for key in ('expected_revision','selected_keys','secret_refs')}
    prior=session.scalar(select(LegacySupplementBatch).where(LegacySupplementBatch.import_id==import_id,LegacySupplementBatch.preview_sha256==body['expected_preview_sha256']))
    if prior:
        if json.loads(prior.request_json)!=request:raise TradeError('LEGACY_PREVIEW_CHANGED','确认参数不匹配',409)
        return json.loads(prior.result_json)
    frozen=preview(session,import_id,request)
    if frozen['preview_sha256']!=body['expected_preview_sha256']:
        raise TradeError('LEGACY_PREVIEW_CHANGED','来源或目标版本已变化，请重新预览',409)
    if not frozen['can_apply']:raise TradeError('LEGACY_SUPPLEMENT_BLOCKED','所选项目有未解决的映射问题')
    row=_row(session,import_id)
    batch_id,now=new_id(),utc_now()
    batch=LegacySupplementBatch(id=batch_id,import_id=import_id,preview_sha256=frozen['preview_sha256'],request_json=canonical(request),preview_json=canonical(frozen),result_json='{}',created_at=now)
    session.add(batch);session.flush()
    targets=[]
    card_targets={}
    # Save round associations before target-setting changes invalidate the projection.
    ordered=sorted(frozen['selected'],key=lambda item: 0 if item['action']['kind']=='round' else 1)
    for item in ordered:
        action=item['action']
        if action['kind']=='card':
            identity=canonical(action['after'])
            target=action['before'] or card_targets.get(identity) or create_card(session,action['after'])
            reused=bool(action['before'] or identity in card_targets)
            card_targets[identity]=target
            target={'type':'inspiration_card','id':target['id'],'revision':target['revision'],'reused':reused}
        elif action['kind']=='round':
            saved=save_round_note(session,row.account_id,action['round_id'],{'expected_revision':action['expected_revision'],'summary':action['after']})
            target={'type':'round_note','id':saved['id'],'round_id':action['round_id'],'revision':saved['revision'],'account_id':row.account_id}
        else:
            saved=settings.save_group(session,action['group'],action['account_id'],action['expected_revision'],action['after'],operation='legacy_import')
            target={'type':'settings','group':action['group'],'account_id':action['account_id'],'revision':saved['revision']}
        session.add(LegacySupplementItem(import_id=import_id,source_key=item['key'],batch_id=batch_id,target_json=canonical(target)))
        targets.append({'source_key':item['key'],'target':target})
    row.revision+=1
    result={'id':batch_id,'import_id':import_id,'revision':row.revision,'preview_sha256':frozen['preview_sha256'],'targets':targets,'created_at':now}
    batch.result_json=canonical(result)
    session.add(AuditEvent(id=new_id(),account_id=row.account_id,entity_type='legacy_supplement',entity_id=batch_id,operation='apply',before_json=canonical(frozen),after_json=canonical(result),created_at=now))
    session.flush()
    return result


def history(session,import_id):
    _row(session,import_id)
    return [{'preview':json.loads(row.preview_json),'result':json.loads(row.result_json)} for row in session.scalars(select(LegacySupplementBatch).where(LegacySupplementBatch.import_id==import_id).order_by(LegacySupplementBatch.created_at.desc()))]
