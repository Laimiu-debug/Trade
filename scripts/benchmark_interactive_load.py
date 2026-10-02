"""TR-020 isolated real-HTTP load measurement; never opens user data.

The only mocked boundary is upstream BaoStock: a local HTTP provider waits before
returning valid daily rows. The application, SQLite writes, pagination, scheduler,
and process-isolated backtest all run their production paths.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def serve(directory, port, provider):
    os.environ.update(TRADE_REBUILD_DATA_DIR=directory, TRADE_REBUILD_PORT=str(port))
    from trade_app.market import baostock_online
    def slow_fetch(symbol, start, end, _directory):
        with urlopen(provider+'/?symbol='+symbol+'&day='+end.isoformat(),timeout=10) as reply:
            return json.load(reply)
    baostock_online.fetch_baostock_rows=slow_fetch
    import uvicorn
    from trade_app.main import create_app
    uvicorn.run(create_app(Path(directory)),host='127.0.0.1',port=port,log_level='warning')


def seed(directory):
    from sqlalchemy import insert
    from trade_app.platform.db import open_database
    from trade_app.platform.types import utc_now
    from trade_app.trading.models import Account, Trade
    from trade_app.reviews.models import DailyReview
    from trade_app.market.service import import_dataset
    from trade_app.research.event_store_models import EventStoreVersion, EventStoreRecord
    engine,factory=open_database(directory)
    now=utc_now();account='bench-real'
    with factory.begin() as session:
        session.add(Account(id=account,name='Isolated performance fixture',kind='real',currency='CNY',input_revision=0,created_at=now))
        session.flush()
        for offset in range(0,100000,5000):
            rows=[]
            for index in range(offset,offset+5000):
                rows.append({'id':f'bench-trade-{index}','account_id':account,
                    'trade_date':(date(2010,1,1)+timedelta(days=index//20)).isoformat(),'sequence':index%20+1,
                    'symbol':'SH600000','name':'fixture','side':'buy' if index%2==0 else 'sell','quantity':100,
                    'price_units':100000,'fee_minor':500,'revision':1,'created_at':now,'updated_at':now})
            session.execute(insert(Trade),rows)
        session.execute(insert(DailyReview),[{'id':f'bench-review-{index}','account_id':account,
            'review_date':(date(2010,1,1)+timedelta(days=index)).isoformat(),'title':'Fixture review',
            'overall_summary':'Synthetic review body. '*60,'created_at':now,'updated_at':now,'revision':1} for index in range(5000)])
        bars=[]
        for index in range(1200):
            day=(date(2020,1,1)+timedelta(days=index)).isoformat()
            close=10+index*.015+math.sin(index*.31)*.4
            bars.append({'event_date':day,'open':f'{close-.03:.4f}','high':f'{close+.15:.4f}',
                'low':f'{close-.15:.4f}','close':f'{close:.4f}','volume':100000+index*100,
                'amount':'2000000','available_at':day+'T07:00:00+00:00'})
        dataset=import_dataset(session,directory,{'symbol':'sh600000','adjustment':'none','bars':bars})
        session.add(EventStoreVersion(id='benchmark-fixture',config_json='{}',code_sha256='fixture-not-a-computed-version',
            profile_id='fixture',profile_revision=0,window_days=60,strict=1,created_at=now))
        session.flush()
        # Read-only summary pagination fixture: all dates precede source bars.
        # No computed events or numerical strategy claims are fabricated.
        raw='{}';checksum=hashlib.sha256(raw.encode()).hexdigest()
        session.execute(insert(EventStoreRecord),[{'id':f'bench-event-{index:05}',
            'version_id':'benchmark-fixture','dataset_id':dataset['id'],'symbol':'sh600000',
            'decision_date':(date(1990,1,1)+timedelta(days=index)).isoformat(),
            'decision_at':(date(1990,1,1)+timedelta(days=index)).isoformat()+'T15:59:59+00:00',
            'source_date':None,'status':'insufficient_history','event_count':0,'risk_count':0,
            'primary_event':'','observed_bars':0,'result_json':raw,'content_sha256':checksum,
            'byte_count':2,'created_at':now} for index in range(10000)])
    engine.dispose()
    return account,dataset['id']


def percentile(values, fraction):
    values=sorted(values)
    return values[max(0,math.ceil(len(values)*fraction)-1)] if values else None


def benchmark(samples):
    import httpx
    import psutil
    directory=Path(tempfile.mkdtemp(prefix='trade-interactive-benchmark-'))
    account,dataset=seed(directory)
    provider_events=[];provider_active=set();provider_serial=[0]
    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            query=parse_qs(urlparse(self.path).query);symbol=query['symbol'][0];day=query['day'][0]
            provider_serial[0]+=1;request_id=provider_serial[0];provider_active.add(request_id)
            started=time.perf_counter()
            try:
                time.sleep(.6)
                raw=json.dumps([{'code':symbol[:2]+'.'+symbol[2:],'date':day,'open':'10','high':'10.2','low':'9.8',
                    'close':'10.1','volume':'1000','amount':'10100','tradestatus':'1'}]).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                provider_events.append({'symbol':symbol,'elapsed_ms':(time.perf_counter()-started)*1000})
            finally: provider_active.discard(request_id)
        def log_message(self,*_args): pass
    provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    with socket.socket() as binding:
        binding.bind(('127.0.0.1',0));port=binding.getsockname()[1]
    log=(directory/'server.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--serve',str(directory),str(port),
        f'http://127.0.0.1:{provider.server_port}'],cwd=ROOT,stdout=log,stderr=log,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    base=f'http://127.0.0.1:{port}'
    errors=[];rows=[];worker_pids=set();peak_rss=0;revision=0
    started=time.perf_counter();calls=0
    try:
        with httpx.Client(base_url=base,timeout=20) as client:
            for _ in range(100):
                try:
                    if client.get('/health').status_code==200: break
                except httpx.HTTPError: pass
                if process.poll() is not None: raise RuntimeError((directory/'server.log').read_text())
                time.sleep(.1)
            token=client.get('/api/v1/session').json()['data']['csrf_token']
            def request(method,path,body=None):
                nonlocal calls
                calls+=1
                headers={'X-CSRF-Token':token,'Idempotency-Key':f'bench-{calls}'}
                response=client.request(method,'/api/v1'+path,json=body,headers=headers)
                response.raise_for_status()
                return response.json()['data']
            enqueue=[]
            for job_index in range(3):
                before=time.perf_counter()
                job=request('POST','/backtests',{'dataset_id':dataset,'strategy_id':'wyckoff_trend_v1',
                    'initial_capital':str(100000+job_index),'holding_bars':5,'max_position_pct':'.95','strict':True,
                    'params':{'min_score':'0','min_event_count':'0'}})
                enqueue.append({'id':job['id'],'elapsed_ms':(time.perf_counter()-before)*1000})
            sync=request('POST','/market/sync-jobs',{'symbols':[f'sh600{index:03}' for index in range(1,51)],
                'provider':'baostock','mode':'full','start_date':'2025-01-01','end_date':'2025-01-02'})
            def state():
                nonlocal peak_rss
                family=[psutil.Process(process.pid),*psutil.Process(process.pid).children(recursive=True)]
                live=[];rss=0
                for child in family:
                    try:
                        rss+=child.memory_info().rss
                        if 'trade_app.research.backtest_worker' in ' '.join(child.cmdline()):
                            worker_pids.add(child.pid);live.append(child.pid)
                    except psutil.Error: pass
                peak_rss=max(peak_rss,rss)
                return live
            for _ in range(100):
                if state() and request('GET','/market/sync-jobs/'+sync['id'])['state']=='running': break
                time.sleep(.05)
            # Three warmups use the same HTTP paths but are not timed samples.
            for _ in range(3):
                request('GET','/research/event-store/records?limit=100&offset=100')
                review=request('PUT',f'/accounts/{account}/daily-reviews/2026-09-26',{'expected_revision':revision,'overall_summary':'Warmup'})
                revision=review['revision']
            for index in range(samples):
                live=state();sync_state=request('GET','/market/sync-jobs/'+sync['id'])['state']
                pair={'index':index,'active_backtest_pids':live,'sync_state':sync_state,'provider_requests_before':sorted(provider_active)}
                for kind,method,path,body in (
                    ('pagination','GET',f'/research/event-store/records?limit=100&offset={(index%10)*900}',None),
                    ('save','PUT',f'/accounts/{account}/daily-reviews/2026-09-26',{'expected_revision':revision,
                        'overall_summary':f'Iteration {index}: '+'Synthetic performance fixture. '*100})):
                    before=time.perf_counter()
                    try:
                        result=request(method,path,body)
                        if kind=='save': revision=result['revision']
                        elif len(result)!=100: raise ValueError('Expected 100 real paginated records')
                        pair[kind+'_ms']=(time.perf_counter()-before)*1000
                    except Exception as exc:
                        errors.append({'index':index,'kind':kind,'error':str(exc)});pair[kind+'_ms']=None
                pair['backtest_pids_after']=state();pair['provider_requests_after']=sorted(provider_active)
                rows.append(pair);time.sleep(.05)
            completed_before_cleanup=[request('GET','/backtests/'+job['id']) for job in enqueue]
            sync_before_cleanup=request('GET','/market/sync-jobs/'+sync['id'])
            for job in completed_before_cleanup:
                if job['state'] in ('queued','running'): request('POST','/backtests/'+job['id']+'/cancel',{})
            if sync_before_cleanup['state'] in ('queued','running'): request('POST','/market/sync-jobs/'+sync['id']+'/cancel',{})
            for _ in range(100):
                final_sync=request('GET','/market/sync-jobs/'+sync['id'])
                if not state() and final_sync['state'] not in ('queued','running','cancelling'): break
                time.sleep(.05)
        overlapping=[row for row in rows if set(row['active_backtest_pids']) & set(row['backtest_pids_after'])
            and set(row['provider_requests_before']) & set(row['provider_requests_after'])]
        def stats(selected,key):
            values=[row[key] for row in selected if row.get(key) is not None]
            return {'count':len(values),'p50_ms':percentile(values,.50),'p95_ms':percentile(values,.95),'max_ms':max(values) if values else None}
        result={'recorded_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'directory':str(directory),
            'machine':{'os':platform.platform(),'processor':platform.processor(),'logical_cpus':psutil.cpu_count(),
                'physical_cpus':psutil.cpu_count(logical=False),'ram_bytes':psutil.virtual_memory().total,
                'python':sys.version,'disk_path':str(directory),'disk_free_bytes':psutil.disk_usage(str(directory)).free},
            'dataset':{'trades':100000,'daily_reviews':5000,'event_summary_rows':10000,
                'market_symbols':1,'bars_per_symbol':1200,'event_rows_are_explicit_metadata_fixtures':True},
            'load':{'backtest_strategy':'wyckoff_trend_v1','queued_backtests':3,'cpu_workers':1,
                'slow_provider':'local HTTP fixture; no public provider network','provider_delay_seconds':.6,'sync_symbols':50,
                'provider_completed_requests':len(provider_events),'actual_backtest_child_pids':sorted(worker_pids)},
            'samples':samples,'overlapping_samples':len(overlapping),'errors':errors,
            'all_samples':{key:stats(rows,key+'_ms') for key in ('pagination','save')},
            'backtest_and_sync_overlap':{key:stats(overlapping,key+'_ms') for key in ('pagination','save')},
            'enqueue':enqueue,'peak_application_process_tree_rss_bytes':peak_rss,
            'elapsed_seconds':time.perf_counter()-started,'backtest_states_before_cleanup':[{'id':job['id'],'state':job['state'],'error':job['error']} for job in completed_before_cleanup],
            'sync_before_cleanup':sync_before_cleanup,'rows':rows,
            'limitations':['Not a 5000-symbol x 1000-day market-scale benchmark','HTTP loopback excludes browser rendering latency',
                'Full transaction durability retained (SQLite WAL synchronous FULL)','Fixture metadata pagination is event-store summaries, not a claimed account-trade pagination API']}
        (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({key:value for key,value in result.items() if key not in ('rows','sync_before_cleanup')},ensure_ascii=False,indent=2))
        if errors or len(overlapping)<max(10,samples//2): raise RuntimeError('Insufficient valid concurrent samples; inspect result.json')
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        provider.shutdown();provider.server_close();log.close()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve': serve(sys.argv[2],int(sys.argv[3]),sys.argv[4])
    else:
        parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--samples',type=int,default=100)
        args=parser.parse_args()
        if not 20<=args.samples<=500: parser.error('--samples must be 20..500')
        benchmark(args.samples)
