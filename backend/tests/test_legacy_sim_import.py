import copy
import json
from decimal import Decimal, ROUND_HALF_UP

import pytest
from sqlalchemy import select, func

from trade_app.legacy_import import service, sim_service
from trade_app.legacy_import.sim_normalizer import normalize
from trade_app.platform.models import AuditEvent
from trade_app.platform.types import TradeError
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.trading.models import Account, Trade
from trade_app.trading.sim_models import SimWallet, SimOrder, SimFill, SimLot
from trade_app.trading import simulation
from test_legacy_import import db, body, save


def fixture():
    return {'schema_version':1,'account':{'initial_capital':'10000','cash':'9189.40','as_of_date':'2025-01-06'},
            'config':{'initial_capital':'10000','commission_rate':'0.0003','min_commission':'5',
                      'stamp_tax_rate':'0.0005','transfer_fee_rate':'0','slippage_rate':'0'},
            'orders':[{'order_id':'buy1','symbol':'600000.SH','side':'buy','quantity':200,'signal_date':'2025-01-01',
                       'submit_date':'2025-01-01','filled_date':'2025-01-02','status':'filled','cash_impact':'-2005'},
                      {'order_id':'sell1','symbol':'600000.SH','side':'sell','quantity':100,'signal_date':'2025-01-02',
                       'submit_date':'2025-01-02','filled_date':'2025-01-03','status':'filled','cash_impact':'1194.40'}],
            'fills':[{'order_id':'buy1','symbol':'600000.SH','side':'buy','quantity':200,'fill_date':'2025-01-02',
                      'fill_price':'10','price_source':'vwap','gross_amount':'2000','net_amount':'-2005',
                      'fee_commission':'5','fee_stamp_tax':'0','fee_transfer':'0'},
                     {'order_id':'sell1','symbol':'600000.SH','side':'sell','quantity':100,'fill_date':'2025-01-03',
                      'fill_price':'12','price_source':'approx','gross_amount':'1200','net_amount':'1194.4',
                      'fee_commission':'5','fee_stamp_tax':'0.6','fee_transfer':'0'}],
            'lots':[{'lot_id':'lot1','symbol':'600000.SH','buy_date':'2025-01-02','available_date':'2025-01-03',
                     'quantity':200,'remaining_quantity':100,'buy_price':'10','unit_cost':'10.025','fee_total':'5'}],
            'closed_trades':[{'symbol':'600000.SH','buy_date':'2025-01-02','buy_price':'10','sell_date':'2025-01-03',
                              'sell_price':'12','quantity':100,'holding_days':1,'pnl_amount':'191.9',
                              'pnl_ratio':str((Decimal('191.9')/Decimal('1002.5')).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP))}]}


def archive(factory, value=None):
    with factory.begin() as session:return save(session,body(value or fixture(),mode='archive_only',account_name=''))


def request(revision=1):return {'expected_revision':revision,'account_name':'完整旧模拟副本'}


def test_replay_exact_cash_fifo_and_no_source_mutation():
    source=fixture();original=copy.deepcopy(source)
    value=normalize(source)
    assert value['can_import'],value['errors']
    assert value['summary']['cash_minor']==918940
    assert value['records']['lots'][0]['cost_minor']==100250
    assert value['records']['fills'][1]['realized_pnl_minor']==19190
    assert value['summary']['initial_date'] is None and source==original


@pytest.mark.parametrize('mutate',[
    lambda x:x['account'].update(cash='9189.41'),
    lambda x:x['fills'][0].update(fee_commission='5.0001'),
    lambda x:x['orders'][0].update(status='pending'),
    lambda x:x['lots'][0].update(available_date='2025-01-07'),
    lambda x:x['lots'][0].update(available_date='2025-01-02'),
    lambda x:x['lots'][0].update(remaining_quantity=200),
    lambda x:x['closed_trades'][0].update(pnl_amount='191.91'),
    lambda x:x['fills'][0].update(order_id='missing'),
    lambda x:x['fills'].reverse(),
    lambda x:x['lots'].__setitem__(0,None),
    lambda x:x['closed_trades'].__setitem__(0,None),
    lambda x:x.update(schema_version=2),
    lambda x:x['orders'][1].update(symbol='sh600000'),
])
def test_invalid_source_rejected_with_specific_details(mutate):
    value=fixture();mutate(value);result=normalize(value)
    assert not result['can_import'] and result['errors'] and result['records'] is None


def test_new_sim_account_once_continue_sell_and_backup(db,tmp_path):
    path,factory=db;imported=archive(factory)
    with factory.begin() as session:
        before=service.get_import(session,imported['id'])['archive']
        frozen=sim_service.preview(session,imported['id'],request())
        assert frozen['can_import']
        assert session.scalar(select(func.count()).select_from(SimWallet))==0
        confirmed={**request(),'expected_preview_sha256':frozen['preview_sha256'],'acknowledge_limitations':True}
        result=sim_service.apply(session,imported['id'],confirmed)
        assert sim_service.apply(session,imported['id'],confirmed)==result
        account_id=result['account_id']
        assert session.get(Account,account_id).kind=='sim'
        assert session.scalar(select(func.count()).select_from(Trade))==0
        assert session.scalar(select(func.count()).select_from(SimFill))==2
        order=session.scalar(select(SimOrder).where(SimOrder.side=='buy'))
        assert simulation.order_data(order)['legacy_origin']['historical_config_unknown']
        assert order.config_json=='{}' and order.config_version==0
        assert not session.scalar(select(AuditEvent).where(AuditEvent.entity_type=='sim_account',AuditEvent.operation=='create'))
        assert service.get_import(session,imported['id'])['archive']==before
        assert service.get_import(session,imported['id'])['sim_promotion']['account_id']==account_id
        assert service.export_archive(session,imported['id'])['simulation_mapping']['account_id']==account_id
        order=simulation.create_order(session,account_id,{'symbol':'sh600000','side':'sell','quantity':100,
            'limit_price':'11','signal_date':'2025-01-06','submit_date':'2025-01-06'})
        simulation.fill_order(session,account_id,order['id'],{'expected_revision':1,'fill_date':'2025-01-06','fill_price':'11'})
        assert session.get(SimWallet,account_id).cash_minor==1028385
        assert session.scalar(select(SimLot)).remaining_qty==0
    restored=tmp_path/'restore';restore_to_new_directory(create_backup(path),restored)
    engine,other=open_database(restored)
    try:
        with other() as session:
            assert service.get_import(session,imported['id'])['sim_promotion']['account_id']==account_id
            assert len(service.export_archive(session,imported['id'])['simulation_mapping']['mappings'])==3
    finally:engine.dispose()


def test_ack_hash_revision_and_second_account_rejected(db):
    _,factory=db;imported=archive(factory)
    with factory.begin() as session:
        value=sim_service.preview(session,imported['id'],request())
        confirmed={**request(),'expected_preview_sha256':value['preview_sha256'],'acknowledge_limitations':True}
        for changed in ({'expected_revision':2},{'account_name':'changed'},{'acknowledge_limitations':False},{'expected_preview_sha256':'0'*64}):
            with pytest.raises(TradeError):sim_service.apply(session,imported['id'],{**confirmed,**changed})
            assert session.scalar(select(func.count()).select_from(SimWallet))==0
        sim_service.apply(session,imported['id'],confirmed)
        with pytest.raises(TradeError):sim_service.preview(session,imported['id'],request(2))
