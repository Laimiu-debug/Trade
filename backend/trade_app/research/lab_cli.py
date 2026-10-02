"""Portable experiment CLI with durable, hash-checked five-date checkpoints."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from trade_app.platform.compute_process import ComputeBudget, run_json_process
from trade_app.platform.types import TradeError
from trade_app.research.lab_context import MAX_INPUT_BYTES, code_sha256, normalize_input, portfolio_context
from trade_app.research.lab_domain import LIMITATIONS, VERSION, band_statistics
from trade_app.research.portfolio_domain import canonical, digest, initial_checkpoint, summary

BUDGET = ComputeBudget(version='laboratory-five-date-v1', timeout_seconds=120,
    input_bytes=32 * 1024 * 1024, output_bytes=4 * 1024 * 1024)
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


def read_json(path, maximum=MAX_INPUT_BYTES):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise TradeError('LAB_FILE_INVALID', 'JSON文件缺失、为链接或超过读取预算')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise TradeError('LAB_JSON_INVALID', 'JSON无效、包含重复字段或非有限数值') from exc


def atomic_json(path, value):
    raw = canonical(value).encode('utf-8')
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise TradeError('LAB_OUTPUT_LIMIT', '单份实验产物超过128MiB')
    fd, name = tempfile.mkstemp(prefix='.lab-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def output_lock(folder):
    """OS lock releases after interruption; a stale marker never requires deletion."""
    path = folder / '.lab.lock'
    if path.is_symlink():
        raise TradeError('LAB_LOCK_INVALID', '实验锁不能为符号链接')
    stream = path.open('a+b')
    locked = False
    try:
        stream.seek(0)
        if stream.read(1) == b'':
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise TradeError('LAB_BUSY', '该实验正由另一进程运行', 409) from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def compute(payload):
    attempt = uuid.uuid4().hex
    result = run_json_process('trade_app.research.lab_worker', {**payload, 'attempt_id': attempt}, budget=BUDGET)
    data = result.value
    if not data.get('ok') or data.get('attempt_id') != attempt:
        raise TradeError((data.get('error') or {}).get('code', 'LAB_WORKER_FAILED'), '实验子进程未成功完成当前批次')
    return data['result']


def replay_chunks(folder, context, input_hash):
    state = initial_checkpoint(context)
    records, previous = [], input_hash
    files = sorted(folder.glob('chunk-*.json'))
    for index, path in enumerate(files):
        if path.name != f'chunk-{index:04d}.json':
            raise TradeError('LAB_CHECKPOINT_GAP', '检查点序号不连续', 409)
        record = read_json(path, 4 * 1024 * 1024)
        body = {key: value for key, value in record.items() if key != 'sha256'}
        result = record.get('result', {})
        checkpoint = result.get('checkpoint', {})
        expected_cursor = min(state['cursor'] + 5, len(context['calendar']))
        if record.get('sha256') != digest(body) or body.get('previous_sha256') != previous or body.get('input_sha256') != input_hash or body.get('ordinal') != index or checkpoint.get('cursor') != expected_cursor or expected_cursor <= state['cursor']:
            raise TradeError('LAB_CHECKPOINT_CHANGED', '检查点内容、顺序或输入摘要校验失败', 409)
        expected_dates = context['calendar'][state['cursor']:expected_cursor]
        if [row['date'] for row in result.get('equity', [])] != expected_dates or result.get('done') != (expected_cursor == len(context['calendar'])):
            raise TradeError('LAB_CHECKPOINT_CHANGED', '检查点日期范围校验失败', 409)
        records.append(record)
        state, previous = checkpoint, record['sha256']
    return state, records, previous


def run_variant(folder, context, *, window, target, budget_bytes, checkpoint_limit=None):
    folder.mkdir(exist_ok=True)
    if folder.is_symlink():
        raise TradeError('LAB_OUTPUT_INVALID', '实验产物目录不能为链接')
    input_hash = digest(context)
    state, records, previous = replay_chunks(folder, context, input_hash)
    written = 0
    while state['cursor'] < len(context['calendar']):
        if checkpoint_limit is not None and written >= checkpoint_limit:
            return None, budget_bytes, written
        result = compute({'operation': 'chunk', 'context': context, 'checkpoint': state})
        if result['checkpoint']['cursor'] != min(state['cursor'] + 5, len(context['calendar'])):
            raise TradeError('LAB_WORKER_CHECKPOINT_INVALID', '计算结果未推进预期日期')
        record = {'ordinal': len(records), 'input_sha256': input_hash, 'previous_sha256': previous, 'result': result}
        record['sha256'] = digest(record)
        budget_bytes += len(canonical(record).encode('utf-8'))
        if budget_bytes > MAX_ARTIFACT_BYTES:
            raise TradeError('LAB_OUTPUT_LIMIT', '实验产物超过128MiB，已保留先前检查点')
        atomic_json(folder / f'chunk-{len(records):04d}.json', record)
        records.append(record)
        state, previous = result['checkpoint'], record['sha256']
        written += 1
    equity = [row for record in records for row in record['result']['equity']]
    from trade_app.research.lab_score_validation import build_trade_summary
    value = {'summary': summary(context, state), 'bands': band_statistics(equity, context['config']['initial_capital'], window=window, target=target),
        'trade_statistics': build_trade_summary(context, records, window=window, target=target),
        'input_sha256': input_hash, 'last_checkpoint_sha256': previous, 'checkpoint_count': len(records),
        'parameters': {key: context[key] for key in ('strategy_id', 'params', 'filters', 'config')}}
    atomic_json(folder / 'summary.json', value)
    return value, budget_bytes, written


def write_html(folder, artifact):
    if (folder / 'report.html').is_symlink():
        raise TradeError('LAB_OUTPUT_INVALID', '报告文件不能为符号链接')
    text = html.escape(json.dumps(artifact, ensure_ascii=False, indent=2))
    contents = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<title>独立策略实验报告</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:16px}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}p{line-height:1.6}</style><h1>独立策略实验报告</h1>'
        '<p>固定历史样本。排名与收益仅描述本次输入；参数比较为样本内结果。完整行情、参数及检查点保存在相邻 JSON 文件。</p>'
        '<pre>' + text + '</pre></html>')
    (folder / 'report.html').write_text(contents, encoding='utf-8')


def run_experiment(raw, output, operation, *, resume=False, checkpoint_limit=None):
    if operation not in ('scan', 'morning', 'diagnose', 'backtest', 'optimize', 'target-fit', 'diagnose-position', 'verify-rhythm', 'validate-chart'):
        raise TradeError('UNKNOWN_LAB_OPERATION', '未知实验操作')
    value = normalize_input(raw)
    if operation == 'validate-chart' and value['strategy_id'] != 'chart_volume_swing_v1':
        raise TradeError('LAB_CHART_REQUIRED', 'validate-chart只接收独立图形评分策略')
    if operation == 'target-fit' and not value.get('target_fit'):
        raise TradeError('LAB_FIT_REQUIRED', 'target-fit需要明确target_fit人工目标和参数组')
    if operation == 'verify-rhythm' and not value.get('rhythm_verification'):
        raise TradeError('LAB_VERIFY_REQUIRED', 'verify-rhythm需要明确rhythm_verification的WANT/REJECT')
    if operation == 'diagnose-position':
        if not value.get('step1'): raise TradeError('LAB_STEP1_REQUIRED', 'position诊断需要明确Step1门槛和股本来源')
        value['end_date'] = min(value['end_date'], value['as_of_date'])
        value = normalize_input(value)
    code = code_sha256()
    manifest = {'format': 'trade-lab-artifact-v1', 'version': VERSION, 'operation': operation,
        'input': value, 'input_sha256': digest(value), 'code_sha256': code,
        'resource_budget': BUDGET.to_dict(), 'limitations': LIMITATIONS}
    if output.is_symlink():
        raise TradeError('LAB_OUTPUT_INVALID', '输出目录不能为符号链接')
    if resume:
        if not output.is_dir() or read_json(output / 'manifest.json', 32 * 1024 * 1024) != manifest:
            raise TradeError('LAB_RESUME_MISMATCH', '输入、代码、操作或预算已变化，请建立新的实验目录', 409)
    else:
        output.mkdir(parents=True, exist_ok=False)
    with output_lock(output):
        if not resume:
            atomic_json(output / 'manifest.json', manifest)
        try:
            atomic_json(output / 'progress.json', {'state': 'running', 'operation': operation})
            context = portfolio_context(value)
            if operation in ('scan', 'morning', 'diagnose'):
                artifact = {'operation': operation, 'input_sha256': digest(value), 'code_sha256': code,
                    **compute({'operation': 'scan', 'context': context, 'as_of_date': value['as_of_date']}),
                    'limitations': LIMITATIONS}
            elif operation == 'target-fit':
                artifact = {'operation': operation, 'input_sha256': digest(value), 'code_sha256': code,
                    **compute({'operation': 'target-fit', 'context': context, 'request': value['target_fit']})}
            elif operation == 'verify-rhythm':
                artifact = {'operation': operation, 'input_sha256': digest(value), 'code_sha256': code,
                    **compute({'operation': 'verify-rhythm', 'context': context, 'request': value['rhythm_verification']})}
            elif operation == 'diagnose-position':
                from trade_app.research.lab_position import diagnostic_context, build_diagnostic
                size = sum(path.stat().st_size for path in output.rglob('chunk-*.json'))
                remaining, paths = checkpoint_limit, {}
                for mode in ('static', 'position'):
                    candidate = diagnostic_context(context, mode)
                    result, size, written = run_variant(output / mode, candidate, window=value['window_sample_days'],
                        target=value['target'], budget_bytes=size, checkpoint_limit=remaining)
                    if remaining is not None: remaining -= written
                    if result is None:
                        atomic_json(output / 'progress.json', {'state': 'paused', 'path': mode})
                        return {'state': 'paused', 'output': str(output)}
                    _, paths[mode], _ = replay_chunks(output / mode, candidate, digest(candidate))
                plain = compute({'operation': 'scan', 'context': context, 'as_of_date': value['as_of_date']})
                artifact = {'operation': operation, 'input_sha256': digest(value), 'code_sha256': code,
                    **build_diagnostic(context, paths, plain)}
            else:
                variants = value['variants'] if operation == 'optimize' else [{key: value[key] for key in ('params', 'config', 'filters')}]
                size = sum(path.stat().st_size for path in output.rglob('chunk-*.json'))
                results, remaining = [], checkpoint_limit
                for index, variant in enumerate(variants):
                    result, size, written = run_variant(output / f'variant-{index:03d}', portfolio_context(value, variant),
                        window=value['window_sample_days'], target=value['target'], budget_bytes=size, checkpoint_limit=remaining)
                    if remaining is not None:
                        remaining -= written
                    if result is None:
                        atomic_json(output / 'progress.json', {'state': 'paused', 'variant': index, 'completed_variants': len(results)})
                        return {'state': 'paused', 'output': str(output)}
                    results.append({'ordinal': index, **result})
                ordered = sorted(results, key=lambda row: (-row['bands']['target_reached_count'],
                    -float(row['summary']['total_return']), float(row['summary']['max_drawdown']), row['ordinal']))
                artifact = {'operation': operation, 'input_sha256': digest(value), 'code_sha256': code,
                    'ranking_scope': 'in_sample_only', 'ranking_rule': 'complete_band_target_hits_then_return_then_drawdown_then_ordinal',
                    'results': results, 'ranking': [row['ordinal'] for row in ordered], 'limitations': LIMITATIONS}
                if operation == 'validate-chart':
                    from trade_app.research.lab_score_validation import build_score_validation
                    _, records, _ = replay_chunks(output / 'variant-000', context, digest(context))
                    artifact['score_validation'] = build_score_validation(context, records)
            if value.get('preset_adoption'):
                from trade_app.research.lab_presets import preset_catalog
                artifact['preset_adoption'] = value['preset_adoption']
                artifact['preset_reference'] = next(row for row in preset_catalog()['presets'] if row['id'] == value['preset_adoption']['id'])
                artifact['preset_note'] = '记录采用来源；可编辑输入的最终参数以本次manifest为准，不宣称旧算法等价'
            if code_sha256() != code:
                raise TradeError('LAB_CODE_CHANGED', '计算期间实验代码变化，拒绝发布结果；原检查点保留', 409)
            atomic_json(output / 'result.json', artifact)
            write_html(output, artifact)
            atomic_json(output / 'progress.json', {'state': 'succeeded', 'result_sha256': digest(artifact)})
            return {'state': 'succeeded', 'output': str(output), 'result_sha256': digest(artifact)}
        except BaseException as exc:
            atomic_json(output / 'progress.json', {'state': 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                'error_code': getattr(exc, 'code', type(exc).__name__)})
            raise


def freeze_manifests(paths, output):
    datasets = []
    if not 1 <= len(paths) <= 64:
        raise TradeError('LAB_SAMPLE_LIMIT', '需要1至64份原始行情manifest')
    for path in paths:
        item = read_json(path)
        if item.get('schema_version') != 1 or item.get('adjustment') != 'none':
            raise TradeError('LAB_MARKET_FORMAT', '只接受新项目schema_version=1未复权行情manifest')
        raw_sha = __import__('hashlib').sha256(path.read_bytes()).hexdigest()
        if len(path.stem) == 64 and path.stem != raw_sha:
            raise TradeError('LAB_DATASET_CHANGED', '行情文件名与内容哈希不一致')
        datasets.append({'symbol': item['symbol'], 'bars': item['bars'], 'source_id': raw_sha})
    value = normalize_input({'format': 'trade-lab-input-v1', 'datasets': datasets})
    # Exclusive creation protects existing hand-authored experiments.
    with output.open('x', encoding='utf-8') as stream:
        stream.write(canonical(value))
    return {'state': 'frozen', 'input': str(output), 'input_sha256': digest(value)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Trade 独立图形量价/混合波段实验室（离线、版本化、可恢复）')
    commands = parser.add_subparsers(dest='operation', required=True)
    freeze = commands.add_parser('freeze', help='从已导入的新行情manifest构造独立实验输入')
    freeze.add_argument('--manifest', action='append', required=True, type=Path)
    freeze.add_argument('--output', required=True, type=Path)
    commands.add_parser('presets', help='列出4套旧晨报预设、可执行映射与明确差异')
    adopt = commands.add_parser('adopt-preset', help='显式生成采用预设的新输入文件，原输入不变')
    adopt.add_argument('--input', required=True, type=Path)
    adopt.add_argument('--output', required=True, type=Path)
    adopt.add_argument('--preset', required=True)
    adopt.add_argument('--accept-adapted', action='store_true')
    for name in ('scan', 'morning', 'diagnose', 'backtest', 'optimize', 'target-fit', 'diagnose-position', 'verify-rhythm', 'validate-chart'):
        command = commands.add_parser(name)
        command.add_argument('--input', required=True, type=Path)
        command.add_argument('--output', required=True, type=Path)
        command.add_argument('--resume', action='store_true')
        command.add_argument('--checkpoint-limit', type=int, help='本次最多提交的5日期检查点，之后可resume')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'freeze':
            result = freeze_manifests(args.manifest, args.output)
        elif args.operation == 'presets':
            from trade_app.research.lab_presets import preset_catalog
            result = preset_catalog()
        elif args.operation == 'adopt-preset':
            from trade_app.research.lab_presets import adopt_preset
            value = adopt_preset(read_json(args.input), args.preset, accept_adapted=args.accept_adapted)
            with args.output.open('x', encoding='utf-8') as stream: stream.write(canonical(value))
            result = {'state': 'adopted', 'input': str(args.output), 'input_sha256': digest(value), 'preset_adoption': value['preset_adoption']}
        else:
            if args.checkpoint_limit is not None and args.checkpoint_limit < 1:
                raise TradeError('LAB_CHECKPOINT_LIMIT', '检查点上限必须为正整数')
            result = run_experiment(read_json(args.input), args.output.resolve(), args.operation,
                resume=args.resume, checkpoint_limit=args.checkpoint_limit)
        print(canonical(result))
        return 0
    except (TradeError, OSError, ValueError) as exc:
        print(canonical({'state': 'failed', 'error': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('实验已中断，已完成检查点保留。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
