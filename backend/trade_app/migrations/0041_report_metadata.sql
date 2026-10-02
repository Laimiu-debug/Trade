ALTER TABLE research_reports ADD COLUMN metadata_json TEXT;
UPDATE research_reports SET metadata_json = json_object(
 'strategy_id', json_extract(payload_json, '$.run.strategy_id'),
 'strategy_version', json_extract(payload_json, '$.run.strategy_version'),
 'symbol', json_extract(payload_json, '$.dataset.symbol'),
 'first_date', json_extract(payload_json, '$.dataset.first_date'),
 'last_date', json_extract(payload_json, '$.dataset.last_date'),
 'scope', 'single_symbol_backtest',
 'summary', json_object(
  'initial_capital', json_extract(payload_json, '$.run.result.initial_capital'),
  'ending_assets', json_extract(payload_json, '$.run.result.ending_assets'),
  'total_return', json_extract(payload_json, '$.run.result.total_return'),
  'max_drawdown', json_extract(payload_json, '$.run.result.max_drawdown'),
  'trade_count', json_extract(payload_json, '$.run.result.trade_count'),
  'win_rate', json_extract(payload_json, '$.run.result.win_rate'),
  'quality_flags', json_extract(payload_json, '$.run.result.quality_flags')
 )) WHERE json_valid(payload_json);
