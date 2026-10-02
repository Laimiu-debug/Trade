# F05 在线分时与缓存验收

## 与旧功能的关系

旧 final-trade `store.get_intraday_payload` 先读通达信 LC1；缺失时可取最新日线并生成近似分时。本轮保留新版的真实 LC1 双击入口，增加明确按钮触发的在线来源与本地缓存。缺失日期不会改成最近一天，来源异常不会变成空成功或近似价格。

官方映射依据（2026-09-26 核对）：

- [AKShare 股票分时文档](https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock.md)：`stock_zh_a_hist_min_em` 的一分钟来源、不复权、近期五个交易日、成交量单位手；文档明确历史开盘价可能为 0。
- [AKShare 指数分时文档](https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md)：`index_zh_a_hist_min_em` 的成交量手、成交额元与近期范围限制。
- [官方股票适配器](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_em.py) / [官方指数适配器](https://github.com/akfamily/akshare/blob/main/akshare/index/index_zh_em.py)：共同的 `trends2/get` 和八字段结构。新版直接使用有界 HTTP 请求，不依赖旧 SDK 的指数跨市场重试。

## 实际能力

- 仅单证券、单指定日期的原始一分钟观察，固定来源东方财富；不复权。不支持分时回测或自动交易。
- 沪深 A 股；明确带交易所的已列出指数：sh000001、sh000016、sh000300、sh000688、sh000905、sh000852、sz399001、sz399005、sz399006、sz399007。北京、基金及其他指数明确拒绝，不猜测源能力。
- `000001` / `000001.SZ` 是 sz000001 股票；`sh000001` / `000001.SH` 是上证指数。请求 secid 与返回 code/market 都需相符；不跨交易所回退。本地 LC1 输出也保留交易所身份。
- 量保持来源单位“手”，不混称“股”；额为元，股票价格为元、指数价格为点。本地 LC1 量保留“来源原单位”。
- 报告冻结请求时点 UTC；源标签按 Asia/Shanghai 解释，仅保留该时点前且标签所在分钟已经结束的记录，保守排除仍会变化的当前分钟。源历史开/高/低为 0 或缺项转 null，收盘价缺失/无效拒绝；成交量或金额缺失为 null，真实成交量 0 保留。
- 抓取时间不冒充历史可得时间，缓存标注 `historical_availability_unknown`，不转成行情研究数据集。

## 资源和持久化

`intraday_online.py` 两个非等待网络槽；固定 HTTPS 主机、禁止重定向；读空闲 5 秒、连接 3 秒，15 秒流式预算检查。最后一次读取可能再消耗空闲超时，因此不是硬 15 秒 SLA，极端情况约 20 秒。响应不超过 512 KiB，来源最多 1600 行，选中日期最多 500 行。

网络发生在 SQLite 写事务之前，成功结果才在短事务中保存。相同 Idempotency-Key 重放已完成响应不联网，同 key 正在执行返回 409。失败不保存或覆盖旧缓存；本轮不实现网络自动重试或自动轮询。

迁移 `0063_intraday_snapshots.sql` 保存完整冻结 JSON 及 SHA-256 ID；列表只查元数据，最多最近 100 条。详情校验摘要；显式删除不可变缓存；重开数据库和全量备份恢复均保留缓存，无旁路文件。

## API 与入口

- `GET /api/v1/market/intraday/capabilities`：只读来源能力。
- `POST /api/v1/market/intraday/fetch {symbol,date}`：显式联网，CSRF + Idempotency-Key。
- `GET /api/v1/market/intraday/snapshots?symbol=&date=`：只读列表，date 可省略。
- `GET /api/v1/market/intraday/snapshots/{id}`：只读详情。
- `DELETE /api/v1/market/intraday/snapshots/{id}`：显式删除，CSRF + Idempotency-Key。
- 行情页选择固定样本，在“在线分时与缓存”选择日期并点击获取。进入页面、改日期、查看缓存与刷新页面都不触发外部网络。

## 验证与外部可用性

- `backend/tests/test_intraday_online.py` 22 项通过：身份冲突/能力拒绝、单位与缺失、当前/未来分钟、时间区、来源格式与重复、边界与超时、别名缓存/摘要/备份/删除、API 幂等/CSRF、获取阶段不持有 SQLite 写锁、失败保留旧缓存。
- `intraday-workspace.test.tsx` 3 项通过；TypeScript、架构检查通过。
- `frontend/scripts/smoke-intraday.mjs` 使用隔离目录、真实 Edge/HTTP/SQLite 与确定性提供方夹具；覆盖按钮联网、PIT/缺失/单位、失败保留、重新载入不重放、同号指数股票隔离、删除和 375px 布局。该脚本不访问公网。本轮 build792 真实浏览器实际通过，隔离目录 `trade-intraday-ui-rq48AB`；确认失败后仍显示旧快照、重载不重放请求，删除股票缓存不影响同号指数缓存。
- 2026-09-26 06:58 UTC 进行了小范围真实来源探测：sh000001 指定 2026-09-25 未返回该日分时；随后一次请求连接不可用。没有把该日认定为交易日或补造行情，也没有据此声明来源在生产中可用。公网状态与隔离测试通过分开记录。

## 未扩大的范围

不提供任意历史分钟全量、其他分钟周期、自动来源切换、未核验证券能力或历史分钟可得性证明。缺失时可继续查看用户已有真实 LC1；是否支持更多提供方和交易所需要各自身份/单位与可用性验收。
