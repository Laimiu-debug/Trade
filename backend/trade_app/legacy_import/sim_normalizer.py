"""Validate a complete final-trade simulation ledger without executing its engine."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from trade_app.legacy_import.normalizer import _day, _int, _symbol, _text
from trade_app.platform.types import TradeError, decimal_value, money_minor, money_text, price_units
from trade_app.trading.simulation import normalized_config

VERSION = 'final-sim-exact-ledger-import-v1'
NOTES = [
    '仅接受完整可核对状态，创建独立新模拟账户；不写真实账本、不执行旧委托。原文件和逻辑档案保持。',
    '旧历史委托未保存当时费率和真实限价；新历史记录标记未知费率，价格栏仅保留成交参考。实际成交费用来自原fill，不用当前配置重算。',
    '旧配置用于新账户后续委托；旧隐含0.5%预检查不等价新版固定现金缓冲，新的cash_buffer明确从0开始。',
    '旧源没有可靠初始日期，因此不补造初始资金审计日期；可继续模拟，但完整历史权益报告将明确提示起点未知。',
    '未成交pending委托、同日买卖、未到可用日期持仓、金额非精确分、坏引用或不守恒状态均拒绝。取消/拒绝委托保留只读档案。',
    '买入批次按原fills顺序重建，逐笔对照剩余lots和closed_trades；相同日的顺序以原列表为证。历史价格源仍标旧来源未经当前行情重新验证。',
]


def _money(value, field, *, signed=False):
    if isinstance(value, bool) or value is None: raise ValueError(field+'金额缺失')
    number=decimal_value(value, field)
    if signed and number < 0: return -money_minor(-number, field, allow_zero=True)
    return money_minor(number, field, allow_zero=True)


def _quantity(value):
    number=_int(value)
    if number % 100:raise ValueError('数量须为100股整数倍')
    return number


def _identity(value):
    value=_text(value,120)
    if not value.strip():raise ValueError('旧委托ID不能为空')
    return value


def _decimal(value):
    if value is None or isinstance(value,bool):raise ValueError('数值缺失')
    return decimal_value(value,'旧模拟数值')


def normalize(payload):
    result={'version':VERSION,'can_import':False,'errors':[],'notes':NOTES,'records':None,'summary':None}
    def error(section,identity,exc):
        result['errors'].append({'section':section,'source_id':str(identity),'message':exc.message if isinstance(exc,TradeError) else str(exc)})
    try:
        if not isinstance(payload,dict):raise ValueError('模拟状态须为对象')
        if type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:raise ValueError('仅识别实际旧schema_version=1；不猜缺失或其他版本')
        account, config=payload['account'],payload['config']
        for section in ('orders','fills','lots','closed_trades'):
            if not isinstance(payload.get(section),list):raise ValueError(section+'缺失完整数组')
        initial=_money(account['initial_capital'],'初始资金');cash=_money(account['cash'],'现金');as_of=_day(account['as_of_date'])
        if initial<=0:raise ValueError('初始资金须为正数')
        if _money(config['initial_capital'],'配置初始资金')!=initial:raise ValueError('账户与配置初始资金不同')
        future_config=normalized_config({'commission_rate':config['commission_rate'],'minimum_commission':config['min_commission'],
            'sell_stamp_rate':config['stamp_tax_rate'],'transfer_rate':config['transfer_fee_rate'],
            'slippage_rate':config['slippage_rate'],'cash_buffer':'0'})
    except (KeyError,ValueError,TypeError,TradeError) as exc:
        error('account/config','',exc);return result
    orders={}
    for index,row in enumerate(payload['orders']):
        try:
            if not isinstance(row,dict):raise ValueError('委托须为对象')
            identity=_identity(row.get('order_id'))
            if identity in orders:raise ValueError('委托ID重复')
            status=row.get('status')
            if status=='pending':raise ValueError('存在未成交委托；旧现金未预留，不能自动转成新版委托。请在旧资料副本中结算/撤销后重新导出')
            if status not in ('filled','cancelled','rejected'):raise ValueError('未知委托状态')
            value={'source_id':identity,'status':status}
            if status=='filled':
                if row.get('side') not in ('buy','sell'):raise ValueError('方向无效')
                signal,submit,filled=(_day(row.get(key)) for key in ('signal_date','submit_date','filled_date'))
                if not signal<=submit<=filled<=as_of:raise ValueError('信号/提交/成交/模拟时钟日期关系无效')
                value.update(symbol=_symbol(row.get('symbol')),raw_symbol=row['symbol'],side=row['side'],quantity=_quantity(row.get('quantity')),
                    signal_date=signal,submit_date=submit,fill_date=filled,cash_impact=_money(row.get('cash_impact'),'委托现金影响',signed=True),
                    estimated_price=row.get('estimated_price'),status_reason=row.get('status_reason'))
            orders[identity]=value
        except (KeyError,ValueError,TypeError,TradeError) as exc:error('orders',row.get('order_id',index) if isinstance(row,dict) else index,exc)
    fills=[];seen=set()
    for index,row in enumerate(payload['fills']):
        try:
            if not isinstance(row,dict):raise ValueError('成交须为对象')
            identity=_identity(row.get('order_id'));order=orders.get(identity)
            if identity in seen:raise ValueError('旧引擎每单一次完整成交；重复fill拒绝')
            seen.add(identity)
            if not order or order['status']!='filled':raise ValueError('成交没有唯一filled委托')
            if row.get('symbol')!=order['raw_symbol'] or row.get('side')!=order['side'] or _quantity(row.get('quantity'))!=order['quantity'] or _day(row.get('fill_date'))!=order['fill_date']:
                raise ValueError('成交与委托证券/方向/数量/日期不一致')
            units=price_units(row.get('fill_price'));gross=_money(row.get('gross_amount'),'成交金额')
            if Decimal(units)*order['quantity']/100 != gross:raise ValueError('成交价×数量与gross_amount不一致')
            fees=[_money(row.get(key),key) for key in ('fee_commission','fee_stamp_tax','fee_transfer')]
            net=_money(row.get('net_amount'),'净现金影响',signed=True)
            expected=-(gross+sum(fees)) if order['side']=='buy' else gross-sum(fees)
            if net!=expected or net!=order['cash_impact']:raise ValueError('成交费用/净金额/委托现金影响不守恒')
            if row.get('price_source') not in ('vwap','approx'):raise ValueError('旧价格来源不是vwap/approx')
            fills.append({**order,'index':index,'price_units':units,'gross_minor':gross,'commission_minor':fees[0],'stamp_minor':fees[1],
                'transfer_minor':fees[2],'net_minor':net,'price_source':row['price_source'],'warning':row.get('warning')})
        except (KeyError,ValueError,TypeError,TradeError) as exc:error('fills',row.get('order_id',index) if isinstance(row,dict) else index,exc)
    for identity,order in orders.items():
        if order['status']=='filled' and identity not in seen:error('orders',identity,ValueError('filled委托缺少成交'))
    if result['errors']:return result
    lots=[];closed=[];balance=initial;previous='';canonical_raw={}
    try:
        for fill in fills:
            if fill['fill_date']<previous:raise ValueError('fills日期倒退，不能重排原现金与FIFO时序')
            previous=fill['fill_date'];symbol=fill['symbol']
            if symbol in canonical_raw and canonical_raw[symbol]!=fill['raw_symbol']:raise ValueError('旧账本存在同一规范证券的不同原代码，不能猜原引擎是否将其合并')
            canonical_raw[symbol]=fill['raw_symbol']
            balance+=fill['net_minor']
            if balance<0:raise ValueError('逐笔重放出现负现金')
            if fill['side']=='buy':
                cost=-fill['net_minor']
                lots.append({'source_buy_order_id':fill['source_id'],'symbol':symbol,'acquired_date':fill['fill_date'],
                    'quantity':fill['quantity'],'remaining_qty':fill['quantity'],'cost_minor':cost,
                    'original_cost_minor':cost,'price_units':fill['price_units'],'fee_minor':cost-fill['gross_minor'],
                    'unit_cost':(Decimal(cost)/100/fill['quantity']).quantize(Decimal('.00000001'),rounding=ROUND_HALF_UP)})
                fill['allocations']=None;fill['realized_pnl_minor']=None
                continue
            remaining=fill['quantity'];allocations=[]
            for lot in lots:
                if lot['symbol']!=symbol or lot['remaining_qty']==0:continue
                if lot['acquired_date']>=fill['fill_date']:raise ValueError('卖出触及当日批次，旧T+1回退不迁为新版可交易事实')
                take=min(remaining,lot['remaining_qty'])
                cost=lot['unit_cost']*take
                cost_minor=_money(cost,'分配买入成本')
                fees=Decimal(fill['commission_minor']+fill['stamp_minor']+fill['transfer_minor'])*take/fill['quantity']
                if fees!=fees.to_integral_value():raise ValueError('批次费用无法精确到分，拒绝静默舍入')
                gross=Decimal(fill['gross_minor'])*take/fill['quantity']
                if gross!=gross.to_integral_value():raise ValueError('批次卖出金额无法精确到分')
                pnl=int(gross-fees)-cost_minor
                part={'source_buy_order_id':lot['source_buy_order_id'],'buy_date':lot['acquired_date'],'quantity':take,
                    'cost_minor':cost_minor,'sell_gross_minor':int(gross),'sell_fees_minor':int(fees),'pnl_minor':pnl}
                allocations.append(part)
                closed.append({'symbol':symbol,'buy_date':lot['acquired_date'],'buy_price_units':lot['price_units'],'sell_date':fill['fill_date'],
                    'sell_price_units':fill['price_units'],'quantity':take,'pnl_minor':pnl,'pnl_ratio':(Decimal(pnl)/cost_minor).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP) if cost_minor else Decimal(0)})
                lot['remaining_qty']-=take;lot['cost_minor']-=cost_minor;remaining-=take
                if lot['cost_minor']<0 or (lot['remaining_qty']==0 and lot['cost_minor']!=0):raise ValueError('单位成本舍入导致买入成本无法精确守恒')
                if not remaining:break
            if remaining:raise ValueError('卖出数量超过完整买入记录')
            fill['allocations']=allocations;fill['realized_pnl_minor']=sum(part['pnl_minor'] for part in allocations)
        if balance!=cash:raise ValueError('初始资金+全部净成交金额与当前现金不一致')
    except (KeyError,ValueError,TypeError,TradeError) as exc:error('ledger','',exc);return result
    expected_open=[lot for lot in lots if lot['remaining_qty']]
    if len(expected_open)!=len(payload['lots']):error('lots','',ValueError('剩余批次数量与重放不一致'))
    else:
        lot_ids=set()
        for index,(source,lot) in enumerate(zip(payload['lots'],expected_open)):
            try:
                if not isinstance(source,dict):raise ValueError('批次须为对象')
                identity=_identity(source.get('lot_id'))
                if identity in lot_ids:raise ValueError('批次ID重复')
                lot_ids.add(identity)
                if source.get('symbol')!=canonical_raw[lot['symbol']] or _day(source.get('buy_date'))!=lot['acquired_date'] or _quantity(source.get('quantity'))!=lot['quantity'] or _quantity(source.get('remaining_quantity'))!=lot['remaining_qty'] or price_units(source.get('buy_price'))!=lot['price_units']:
                    raise ValueError('原剩余批次顺序/证券/日期/数量/买价与重放不一致')
                available=_day(source.get('available_date'))
                if not lot['acquired_date']<available<=as_of:raise ValueError('剩余批次尚未到可用日或旧可用日违反T+1，不能改变旧可卖约束')
                if _decimal(source.get('unit_cost'))!=lot['unit_cost'] or _money(source.get('fee_total'),'原批次费用')!=lot['fee_minor'] or _money(lot['unit_cost']*lot['remaining_qty'],'剩余成本')!=lot['cost_minor']:
                    raise ValueError('原剩余批次单位成本/费用与精确分重放不一致')
                lot['source_lot_id']=identity;lot['source_available_date']=available
            except (KeyError,ValueError,TypeError,TradeError) as exc:error('lots',index,exc)
    if len(closed)!=len(payload['closed_trades']):error('closed_trades','',ValueError('旧闭合交易条数与FIFO重放不一致'))
    else:
        for index,(source,expected) in enumerate(zip(payload['closed_trades'],closed)):
            try:
                if not isinstance(source,dict):raise ValueError('闭合交易须为对象')
                actual={'symbol':_symbol(source.get('symbol')),'buy_date':_day(source.get('buy_date')),'buy_price_units':price_units(source.get('buy_price')),
                    'sell_date':_day(source.get('sell_date')),'sell_price_units':price_units(source.get('sell_price')),'quantity':_quantity(source.get('quantity')),
                    'pnl_minor':_money(source.get('pnl_amount'),'原闭合盈亏',signed=True),'pnl_ratio':_decimal(source.get('pnl_ratio'))}
                if actual!=expected:raise ValueError('旧闭合交易字段/成本/费用/盈亏与重放不一致')
                if type(source.get('holding_days')) is not int or source['holding_days']!=(date.fromisoformat(actual['sell_date'])-date.fromisoformat(actual['buy_date'])).days:raise ValueError('持有自然日与原日期不一致')
            except (KeyError,ValueError,TypeError,TradeError) as exc:error('closed_trades',index,exc)
    if result['errors']:return result
    for lot in lots:lot['unit_cost']=str(lot['unit_cost'])
    result.update(can_import=True,records={'fills':fills,'lots':lots,'config':future_config,'initial_minor':initial,'cash_minor':cash,'as_of_date':as_of},
        summary={'fill_count':len(fills),'open_lot_count':len(expected_open),'closed_allocation_count':len(closed),
            'archived_terminal_order_count':sum(order['status']!='filled' for order in orders.values()),'initial_date':None,
            'cash_minor':cash,'cost_minor':sum(lot['cost_minor'] for lot in expected_open),
            'cash':money_text(cash),'cost_basis':money_text(sum(lot['cost_minor'] for lot in expected_open)),'as_of_date':as_of})
    return result
