# 回测矩阵引擎改造 TODO（Matrix Engine）

> 状态：Phase A/B 已落地（执行中）
> 更新时间：2026-02-21
> 目标：在不破坏旧路径的前提下，引入矩阵化信号计算，优先解决 `full_market` 回测速度。

## 1. 改造边界
- [x] 默认开关关闭，确保可回滚。
- [x] 旧路径保持完整可用。
- [ ] 任一偏差超阈值自动回退旧路径（Phase C）。

## 2. Phase A（先落地）

### A1. 新增矩阵基础模块
- [x] 新增 `backend/app/core/backtest_matrix_engine.py`
- [x] 定义 `MatrixBundle(open/high/low/close/volume/valid_mask/dates/symbols)`
- [x] 支持 `npz` 读写缓存
- [x] 缓存 key = `symbols_hash + date_range + data_version + window_set + algo_version`
- [x] 矩阵构建支持并行加载（`TDX_TREND_BACKTEST_MATRIX_BUILD_WORKERS`）。
- [x] 新增矩阵 Bundle 进程内 runtime 缓存（LRU + TTL，减少重复 `npz` 读盘/反序列化）。

### A2. 新增矩阵信号模块
- [x] 新增 `backend/app/core/backtest_signal_matrix.py`
- [x] 输出 `S1~S9`、`in_pool`、`buy_signal`、`sell_signal`、`score`（shape=(T,N)）
- [x] 缺失值/停牌安全处理（统一通过 `valid_mask`）
- [x] 新增矩阵信号进程内 runtime 缓存（同参数重复回测直接复用信号矩阵）。
- [x] 新增矩阵信号磁盘缓存（跨进程/重启复用，减少冷启动重复计算）。

### A3. 接入 store（灰度起步）
- [x] `store.py` 增加开关 `TDX_TREND_BACKTEST_MATRIX_ENGINE`
- [x] Phase A：先在 `mode=full_market && pool_roll_mode=daily` 接矩阵路径
- [x] 开关关闭时 100% 走旧路径
- [x] 矩阵路径失败时可回落旧路径并记录 `notes`

### A4. 执行层改造（保留日循环）
- [x] `backtest_engine.py` 增加“消费矩阵信号切片”的入口
- [x] 避免旧逻辑中的逐股逐日快照重算
- [x] 维持旧执行语义（T+1、止损止盈、最大持仓等）

## 3. Phase B（扩展）
- [x] 扩展到 `full_market + weekly/position`
- [x] weekly 预生成刷新日 mask
- [x] position 只做矩阵切片，不重跑 rolling
- [x] 迁移 `trend_pool` 到同一矩阵信号实现

## 4. Phase C（验证与收敛）

### C1. 测试
- [x] 新增 `backend/tests/test_backtest_matrix_engine.py`
- [ ] 扩展 `backend/tests/test_backtest_api.py` 做旧/新 A/B 严格对比
- [ ] 允许极小浮点差异，保证交易语义一致

### C2. 文档
- [ ] 更新 `docs/BACKTEST_ROLLING_EVENT_STORE_TODO.zh-CN.md`
- [ ] 补充：开关、缓存目录、回滚策略、偏差阈值

## 5. 回滚策略
- [x] 开关默认关闭。
- [x] 失败自动回退旧路径并记录回退原因（`notes`）。
- [ ] 出现偏差超阈值时，自动回退旧路径并告警。

## 6. 输入池缓存（性能附加项）
- [x] 新增刷新日输入池落盘缓存（默认开启）。
- [x] 新增进程内输入池 runtime 缓存（同进程重复任务优先命中，减少重复磁盘/加载器开销）。
- [x] 缓存命中统计写入 `notes`（`hit/miss/write`）。
- [x] 输入池构建支持并行预加载 + 本地缓存复用。
- [x] 配置项：
  - `TDX_TREND_BACKTEST_INPUT_POOL_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_INPUT_POOL_CACHE_DIR`（默认 `~/.tdx-trend/backtest-input-cache`）
  - `TDX_TREND_BACKTEST_INPUT_POOL_CACHE_TTL_SEC`（默认 `43200`）
  - `TDX_TREND_BACKTEST_INPUT_POOL_WORKERS`（默认 `min(8, CPU核数)`）
  - `TDX_TREND_INPUT_POOL_LOAD_WORKERS`（默认 `min(8, CPU核数)`，单次输入池加载并行解析 `.day`）
  - `TDX_TREND_BACKTEST_INPUT_POOL_RUNTIME_TTL_SEC`（默认 `900`）
  - `TDX_TREND_BACKTEST_INPUT_POOL_RUNTIME_MAX_ITEMS`（默认 `512`）
  - `TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE_TTL_SEC`（默认 `900`）
  - `TDX_TREND_BACKTEST_MATRIX_RUNTIME_CACHE_MAX_ITEMS`（默认 `16`）
  - `TDX_TREND_BACKTEST_MATRIX_INCREMENTAL_CACHE`（默认 `1`，允许同参数扩区间走增量拼接）
  - `TDX_TREND_BACKTEST_MATRIX_BUILD_META_MAX_ITEMS`（默认 `128`，构建元信息缓存上限）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE_TTL_SEC`（默认 `900`）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_RUNTIME_CACHE_MAX_ITEMS`（默认 `32`）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_DISK_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_DIR`（默认 `~/.tdx-trend/backtest-signal-matrix-cache`）
  - `TDX_TREND_BACKTEST_SIGNAL_MATRIX_CACHE_TTL_SEC`（默认 `172800`）

## 9. 任务启动性能
- [x] 新增回测预检结果缓存（重复参数短期复用，减少启动前覆盖检查耗时）。
- [x] 支持异步预检开关（开启后先返回 task_id，再在任务线程内执行覆盖预检）。
- [x] 配置项：
  - `TDX_TREND_BACKTEST_PRECHECK_CACHE_TTL_SEC`（默认 `600`）
  - `TDX_TREND_BACKTEST_TASK_PRECHECK_ASYNC`（默认 `0`，开启后异步预检）

## 7. 策略结果持久化（性能附加项）
- [x] 新增趋势选股 run 结果落盘缓存（同参数 + 同 as_of_date 复用 step1~step4 结果）。
- [x] 新增信号结果落盘缓存（`/api/signals`：内存 miss 后可命中磁盘）。
- [x] 新增趋势滚动筛选快照缓存（按刷新日缓存 `trend_step + board_filters + max_symbols` 的 symbols）。
- [x] 新增回测结果落盘缓存（同参数同版本可直接复用回测结果）。
- [x] 命中后保留交易语义并在 `notes` 标记缓存命中来源。
- [x] 配置项：
  - `TDX_TREND_SCREENER_RESULT_CACHE`（默认 `1`）
  - `TDX_TREND_SCREENER_RESULT_CACHE_DIR`（默认 `~/.tdx-trend/screener-result-cache`）
  - `TDX_TREND_SCREENER_RESULT_CACHE_TTL_SEC`（默认 `86400`）
  - `TDX_TREND_SIGNALS_DISK_CACHE`（默认 `1`）
  - `TDX_TREND_SIGNALS_DISK_CACHE_DIR`（默认 `~/.tdx-trend/signals-cache`）
  - `TDX_TREND_SIGNALS_DISK_CACHE_TTL_SEC`（默认 `21600`）
  - `TDX_TREND_BACKTEST_TREND_FILTER_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_TREND_FILTER_CACHE_DIR`（默认 `~/.tdx-trend/backtest-trend-filter-cache`）
  - `TDX_TREND_BACKTEST_TREND_FILTER_CACHE_TTL_SEC`（默认 `86400`）
  - `TDX_TREND_BACKTEST_RESULT_CACHE`（默认 `1`）
  - `TDX_TREND_BACKTEST_RESULT_CACHE_DIR`（默认 `~/.tdx-trend/backtest-result-cache`）
  - `TDX_TREND_BACKTEST_RESULT_CACHE_TTL_SEC`（默认 `172800`）

## 8. 收益平原（参数面板）MVP
- [x] 新增后端模型：`BacktestPlateauRunRequest/Response`。
- [x] `store.py` 新增 `run_backtest_plateau`（复用回测主流程，批量评估参数组合）。
- [x] 新增接口：`POST /api/backtest/plateau`。
- [x] 支持 `max_points` 截断，避免组合爆炸。
- [x] 支持采样模式：`grid` / `lhs`（默认 `lhs`）。
- [x] `lhs` 支持 `sample_points` 与 `random_seed`（可复现采样）。
- [x] 单组失败不终止任务，错误写入 `points.error`。
- [x] 前端页新增收益平原可视化（热力图/排名表）。
- [x] 收益平原新增任务化接口（`start/status/pause/resume/cancel`），切页后任务持续执行。
- [x] 收益平原表单参数本地持久化（切页返回自动恢复）。
- [ ] 指标口径确认（score 公式、稳定性评分、鲁棒性阈值）。
