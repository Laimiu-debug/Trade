import base64
from copy import deepcopy
import io
import json
import stat
import struct
import zipfile

import pytest
from sqlalchemy import func, select

from legacy_oracle import oracle
from trade_app.platform.backup import create_backup, restore_to_new_directory
from trade_app.platform.db import open_database
from trade_app.platform.types import TradeError
from trade_app.research import legacy_report_domain as domain, legacy_report_service as service
from trade_app.research.legacy_report_models import LegacyResearchReport


def _build_original_package():
    # Build through the actual legacy exporter, rather than inventing an input format.
    from app.models import (BacktestRunRequest, BacktestResponse, ReviewStats, ReviewRange,
        EquityPoint, BacktestPlateauParams, BacktestPlateauPoint, BacktestPlateauResponse,
        BacktestPlateauPointDetailResponse, BacktestReportBuildRequest)
    from app.store import InMemoryStore
    request = BacktestRunRequest(date_from='2025-01-01', date_to='2025-02-01', initial_capital=1000)
    result = BacktestResponse(stats=ReviewStats(win_rate=0, total_return=.1, max_drawdown=.03,
        avg_pnl_ratio=0, trade_count=0), trades=[], range=ReviewRange(date_from=request.date_from, date_to=request.date_to),
        equity_curve=[EquityPoint(date='2025-01-01', equity=1000, realized_pnl=0), EquityPoint(date='2025-02-01', equity=1100, realized_pnl=0)])
    params = BacktestPlateauParams(**{key: getattr(request, key) for key in (*domain.PARAMS, 'intraday_trailing_reduce_ratio')})
    point = BacktestPlateauPoint(params=params, stats=result.stats, score=88, plateau_score=75,
        neighbor_pass_rate=.8, detail_key='point_test_01', passes_hard_filters=True)
    failed = point.model_copy(update={'detail_key': None, 'error': '<script>untrusted()</script>', 'passes_hard_filters': False})
    plateau = BacktestPlateauResponse(base_payload=request, total_combinations=2, evaluated_combinations=2,
        points=[point, failed], best_point=point, recommended_point=point, generated_at='2025-02-02T00:00:00Z')
    detail = BacktestPlateauPointDetailResponse(task_id='plateau_test', detail_key=point.detail_key,
        saved_at='2025-02-02T00:00:00Z', params=params, run_request=request, run_result=result)
    source = InMemoryStore.__new__(InMemoryStore)
    built = source.build_backtest_report_package(BacktestReportBuildRequest(
        run_request=request, run_result=result, report_html='<html><script>fetch("https://invalid.example")</script></html>',
        report_xlsx_base64=base64.b64encode(b'opaque-old-excel').decode(), plateau_result=plateau,
        plateau_point_details=[detail], report_id='legacy_report_test'))
    return {'package_base64': built.file_base64, 'request': request.model_dump(exclude_none=True),
            'result': result.model_dump(exclude_none=True), 'plateau': plateau.model_dump(exclude_none=True)}


_PACKAGE = []


@pytest.fixture
def original_package():
    """A real package written by the original exporter (recorded once at tag legacy-final)."""
    if not _PACKAGE:
        _PACKAGE.append(oracle('original_package', _build_original_package))
    built = _PACKAGE[0]
    return base64.b64decode(built['package_base64']), deepcopy(built['request']), deepcopy(built['result']), deepcopy(built['plateau'])


def modify(contents, file_name, change):
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts[file_name] = domain.encode(change(json.loads(parts[file_name])))
    manifest = json.loads(parts['manifest.json'])
    for item in manifest['files']:
        if item['path'] == file_name: item.update(bytes=len(parts[file_name]), sha256=domain.digest(parts[file_name]))
    parts['manifest.json'] = domain.encode(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items(): archive.writestr(name, data)
    return output.getvalue()


def test_actual_old_exporter_roundtrip_preserves_fields_and_never_invents_runtime(original_package):
    raw, request, result, plateau = original_package
    prepared = domain.prepare_import(raw, 'legacy.ftbt')
    assert prepared['preview']['can_convert'] is True
    payload = prepared['payload']
    assert payload['run_request'] == request
    assert payload['run_result'] == result
    assert payload['plateau_result'] == plateau
    assert payload['point_details']['point_test_01']['run_result'] == result
    assert payload['summary']['point_count'] == 2 and payload['summary']['point_detail_count'] == 1
    assert payload['summary']['total_return'] == .1
    assert 'reexecution_unavailable' in payload['quality_flags']
    assert 'legacy_plateau_scores_not_recomputed' in payload['quality_flags']
    assert 'dataset' not in payload and 'code_sha256' not in payload


def test_standalone_plateau_and_laimiu_json_disguised_as_html(original_package):
    raw = domain.encode(original_package[3])
    result = domain.prepare_import(raw, 'plateau.json')
    assert result['preview']['can_convert']
    assert result['preview']['source_format'] == 'final_trade_plateau_json'
    assert result['payload']['run_result'] is None
    assert 'some_plateau_point_details_not_in_package' in result['preview']['quality_flags']
    printed = domain.prepare_import(domain.encode({'username': '用户', 'day': '2025-01-01', 'data': {'trades': []}}), 'daily-review.html')
    assert printed['preview']['source_format'] == 'laimiu_print_json'
    assert not printed['preview']['can_convert']
    assert printed['preview']['missing_fields'] == ['manifest.json', 'run_request.json', 'run_result.json']


@pytest.mark.parametrize('part,change,match', [
    ('run_request.json', lambda value: {**value, 'initial_capital': 'NaN'}, 'initial_capital'),
    ('run_result.json', lambda value: {**value, 'equity_curve': [{'date': '2025-01-01', 'equity': 1}, {'date': '2025-01-01', 'equity': 2}]}, '日期'),
    ('plateau_point_detail__point_test_01.json', lambda value: {**value, 'detail_key': 'wrong_key'}, '明细键'),
    ('plateau_point_detail__point_test_01.json', lambda value: {**value, 'run_request': {**value['run_request'], 'stop_loss': .44}}, '明细请求参数'),
    ('plateau_result.json', lambda value: {**value, 'points': []}, '无法关联'),
])
def test_invalid_content_and_point_association_rejected(original_package, part, change, match):
    with pytest.raises(TradeError, match=match): domain.prepare_import(modify(original_package[0], part, change), 'report.ftbt')


def test_missing_fields_exactly_reported_without_defaulting(original_package):
    raw = modify(original_package[0], 'run_request.json', lambda value: {key: val for key, val in value.items() if key != 'initial_capital'})
    prepared = domain.prepare_import(raw, 'missing.ftbt')
    assert not prepared['preview']['can_convert']
    assert 'report.run_request.initial_capital' in prepared['preview']['missing_fields']
    assert prepared['preview']['summary']['initial_capital'] is None


@pytest.mark.parametrize('path,mode', [('../evil.html', 0), ('nested/report.html', 0), ('report.html', stat.S_IFLNK)])
def test_path_duplicate_or_link_rejected(original_package, path, mode):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original_package[0])) as source, zipfile.ZipFile(output, 'w') as target:
        for item in source.infolist():
            info = zipfile.ZipInfo(path if item.filename == 'report.html' else item.filename)
            if mode and item.filename == 'report.html': info.external_attr = (mode | 0o777) << 16
            target.writestr(info, source.read(item))
    with pytest.raises(TradeError): domain.prepare_import(output.getvalue(), 'bad.ftbt')


def test_hash_mismatch_duplicate_json_and_oversize_rejected(original_package):
    raw = bytearray(original_package[0]); raw[len(raw) // 2] ^= 32
    with pytest.raises(TradeError): domain.prepare_import(bytes(raw), 'broken.ftbt')
    with pytest.raises(TradeError): domain.prepare_import(b'{"base_payload":{},"base_payload":{}}', 'plateau.json')
    with pytest.raises(TradeError): domain.prepare_import(b'x' * (domain.MAX_BYTES + 1), 'large.html')
    changed = modify(original_package[0], 'manifest.json', lambda value: {**value, 'files': [*value['files'], value['files'][0]]})
    with pytest.raises(TradeError): domain.prepare_import(changed, 'duplicate.ftbt')


def test_zip_metadata_rejected_before_zipfile_allocation(original_package, monkeypatch):
    raw = bytearray(original_package[0])
    struct.pack_into('<HH', raw, len(raw) - 22 + 8, 30000, 30000)
    def unexpected(*args, **kwargs): raise AssertionError('ZipFile must not allocate an over-budget index')
    monkeypatch.setattr(domain.zipfile, 'ZipFile', unexpected)
    with pytest.raises(TradeError, match='元数据'): domain.prepare_import(bytes(raw), 'large.ftbt')
    # A forged small EOCD count must also fail before ZipInfo construction.
    struct.pack_into('<HH', raw, len(raw) - 22 + 8, 1, 1)
    with pytest.raises(TradeError, match='目录数量'): domain.prepare_import(bytes(raw), 'lying.ftbt')


def test_small_zip64_ftbt_is_bounded_and_supported(original_package, monkeypatch):
    with zipfile.ZipFile(io.BytesIO(original_package[0])) as source:
        parts = {item.filename: source.read(item) for item in source.infolist()}
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 64)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as target:
        for name, data in parts.items(): target.writestr(name, data)
    assert b'PK\x06\x07' in output.getvalue()
    assert domain.prepare_import(output.getvalue(), 'zip64.ftbt')['preview']['can_convert']


def test_storage_preview_confirmation_dedup_backup_delete_restore_no_task_facts(tmp_path, original_package):
    engine, factory = open_database(tmp_path / 'data')
    prepared = domain.prepare_import(original_package[0], 'original.ftbt')
    from trade_app.research.backtest_models import BacktestRun
    from trade_app.research.plateau_models import PlateauExperiment
    try:
        with factory.begin() as session:
            for expected, ack in (('0' * 64, True), (prepared['preview']['preview_sha256'], False)):
                with pytest.raises(TradeError): service.save_report(session, prepared, expected, 'legacy_readonly', ack)
            assert session.scalar(select(func.count()).select_from(LegacyResearchReport)) == 0
            saved = service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'legacy_readonly', True)
            again = service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'legacy_readonly', True)
            assert saved['id'] == again['id']
            assert session.scalar(select(func.count()).select_from(BacktestRun)) == 0
            assert session.scalar(select(func.count()).select_from(PlateauExperiment)) == 0
            assert 'payload' not in service.list_reports(session)[0]
        archive = create_backup(tmp_path / 'data')
        restore_to_new_directory(archive, tmp_path / 'restored')
        restored_engine, restored_factory = open_database(tmp_path / 'restored')
        try:
            with restored_factory.begin() as session:
                assert service.get_report(session, saved['id']) == saved
                service.delete_report(session, saved['id']); session.flush()
                assert service.list_reports(session) == []
            with restored_factory.begin() as session:
                reimported = service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'legacy_readonly', True)
                assert reimported['id'] == saved['id']
        finally: restored_engine.dispose()
    finally: engine.dispose()


def test_html_archive_only_and_safe_generated_html(original_package, tmp_path):
    html = b'<html><img src=x onerror=alert(1)><script>bad()</script></html>'
    prepared = domain.prepare_import(html, 'original.html')
    assert not prepared['preview']['can_convert']
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            with pytest.raises(TradeError, match='缺失字段'):
                service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'legacy_readonly', True)
            report = service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'archive_only', True)
            assert service.original(session, report['id']) == html
            assert b'<script>' not in domain.render_safe_html(report)
            structured = domain.prepare_import(original_package[0], 'old.ftbt')
            report = service.save_report(session, structured, structured['preview']['preview_sha256'], 'legacy_readonly', True)
            safe = domain.render_safe_html(report)
            assert b'&lt;script&gt;untrusted()&lt;/script&gt;' in safe and b'<script>' not in safe
    finally: engine.dispose()


def test_incomplete_unvalidated_fields_remain_archive_only_without_summary_or_html_crash(tmp_path):
    prepared = domain.prepare_import(domain.encode({'base_payload': {'strategy_id': {'unsafe': '<script>bad</script>'}},
        'points': 'not an array'}), 'incomplete.json')
    assert not prepared['preview']['can_convert']
    assert prepared['preview']['summary']['strategy_id'] is None
    engine, factory = open_database(tmp_path)
    try:
        with factory.begin() as session:
            saved = service.save_report(session, prepared, prepared['preview']['preview_sha256'], 'archive_only', True)
            html = domain.render_safe_html(saved)
            assert b'archive_only' in html and b'<script>' not in html
            assert service.original(session, saved['id']) == prepared['contents']
    finally: engine.dispose()


@pytest.mark.parametrize('change', [lambda value: {**value, 'created_at': 'tomorrow'},
    lambda value: {**value, 'app': {}}, lambda value: {**value, 'app': {'name': 'Final Trade', 'version': []}}])
def test_manifest_origin_metadata_validated(original_package, change):
    with pytest.raises(TradeError): domain.prepare_import(modify(original_package[0], 'manifest.json', change), 'bad.ftbt')


def test_api_explicit_preview_accept_download_nosniff_no_original_html_execution(tmp_path, original_package):
    from trade_app.main import create_app
    from trade_app.api.legacy_report_routes import router
    from test_trade_rebuild import started_client
    app = create_app(tmp_path, auto_rebuild=False)
    if not any(getattr(route, 'path', '') == '/api/v1/research/legacy-reports' for route in app.routes): app.router.routes[0:0] = router.routes
    with started_client(app) as client:
        token = client.get('/api/v1/session').json()['data']['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': 'legacy-test'}
        root = '/api/v1/research/legacy-reports'
        uploaded = {'file': ('original.ftbt', original_package[0], 'application/octet-stream')}
        preview = client.post(root + '/preview', files=uploaded, headers=headers)
        assert preview.status_code == 200, preview.text
        assert client.get(root).json()['data'] == []
        body = {'mode': 'legacy_readonly', 'expected_preview_sha256': preview.json()['data']['preview_sha256'], 'acknowledge_limitations': 'true'}
        saved = client.post(root + '/import', files=uploaded, data=body, headers=headers)
        assert saved.status_code == 200, saved.text
        identifier = saved.json()['data']['id']
        download = client.get(root + '/' + identifier + '/original.bin', params={'name': 'report.html'})
        assert download.headers['content-type'] == 'application/octet-stream'
        assert download.headers['content-disposition'].startswith('attachment;')
        assert download.headers['x-content-type-options'] == 'nosniff'
        assert 'sandbox' in download.headers['content-security-policy']
        assert b'<script>' in download.content  # Original preserved, never returned as HTML.
        safe = client.get(root + '/' + identifier + '/report.html')
        assert b'<script>' not in safe.content and 'sandbox' in safe.headers['content-security-policy']
        assert client.get(root + '/' + identifier + '/original.bin', params={'name': '../report.html'}).status_code == 404
