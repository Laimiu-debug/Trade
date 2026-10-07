# Chat Summary (2026-02-18)

## 1. 本次聊天目标

1. 评估 `E:\Laimiu\turteltrace` 的模块是否适合整合到 `final trade`。
2. 搞清楚 `turteltrace` 如何获取“当日个股价格”和“大盘数据”。
3. 解释 `final trade` 中 “AkShare 跑不通” 的真实原因。
4. 对比两个项目的股票代码/名称库覆盖情况。

## 2. 已确认结论

### 2.1 `turteltrace` 的行情来源

1. `turteltrace` 不是走 AkShare 历史管线，而是前端直接请求东方财富接口。
2. 个股实时价格来自 `push2.eastmoney.com`：`E:/Laimiu/turteltrace/src/services/stockService.ts`
3. 大盘指数与市场概况来自 `push2.eastmoney.com` 指数列表接口，且每 5 分钟自动刷新：`E:/Laimiu/turteltrace/src/components/dashboard/review/sections/MarketDataSection.tsx`
4. 市场快讯来自 `np-weblist.eastmoney.com`：`E:/Laimiu/turteltrace/src/services/newsService.ts`

### 2.2 `final trade` 的数据链路

1. 运行时核心是读取本地数据（TDX 或 AkShare 缓存 CSV）：`E:/Laimiu/final trade/backend/app/tdx_loader.py`
2. AkShare 在当前项目主要用于离线同步脚本，不是每次实时拉取：`E:/Laimiu/final trade/backend/scripts/sync_akshare_daily.py`
3. `POST /api/system/sync-market-data` 当前实现实际走 Baostock：`E:/Laimiu/final trade/backend/app/store.py`、`E:/Laimiu/final trade/backend/app/market_data_sync.py`

### 2.3 为什么会感觉“AkShare 跑不通”

1. 当前配置里 `market_data_source` 是 `tdx_only`，不会使用 AkShare 缓存。
2. 页面“同步市场数据”按钮走的是 Baostock；如果未安装 `baostock` 会失败。
3. 实测 AkShare 脚本本身可跑通（可生成本地 CSV）。

### 2.4 股票代码库覆盖对比

1. `turteltrace` 静态库条目数：5475。
2. 当前 `final trade` 扫描池（`sh+sz`）条目数：5102。
3. 你当前池中“无中文名”仅 1 条：`sz300379`。
4. 该代码在 `turteltrace` 静态库中也没有，无法通过直接合并该库补齐。

### 2.5 对项目定位的影响

1. `turteltrace` 的价值主要是“实时层经验”（实时价/指数/快讯），不是“全 A 历史策略底座”。
2. `final trade` 应继续保持“本地历史数据 + 本地策略计算”为主干。

## 3. 功能整合任务（新增）

### 3.1 建议整合的模块（按优先级）

1. 日复盘 + 周复盘模板体系  
来源：`reviewService.ts`、`weeklyReviewService.ts`、`ReviewTab.tsx`、`WeeklyReviewEditor.tsx`
2. 交易标签体系（情绪/原因/计划）  
来源：`tagService.ts`、`PositionManager.tsx`
3. 股票本地搜索库（代码/拼音/行业）  
来源：`stockDatabase.ts`、`stock-database.json`
4. 分享卡片导出  
来源：`ShareDialog.tsx`、`StockShareDialog.tsx`
5. 新闻面板（可选）  
来源：`newsService.ts`、`NewsFeed.tsx`

### 3.2 整合原则

1. 不直接搬 UI（`turteltrace` 偏 Tailwind/shadcn），在 `final trade` 用 AntD 重做界面。
2. 保留业务模型与流程，替换存储方式（`localStorage` -> 后端 API + 本地 JSON）。
3. 外部行情/新闻请求统一走后端代理，避免前端直连 CORS 和稳定性问题。
4. 历史策略计算仍使用本地数据层（TDX/CSV），实时接口仅做当日展示与辅助决策。

### 3.3 分阶段落地计划

1. Phase A（先做，1 周）  
落地“日/周复盘 + 标签系统”最小可用版本（MVP）。
2. Phase B（次优先，1 周）  
接入“股票搜索库 + 分享卡片”。
3. Phase C（可选，0.5~1 周）  
接入新闻模块（后端代理 + 前端展示）。

### 3.4 每阶段验收标准

1. Phase A  
- 可创建、编辑、查看日复盘与周复盘  
- 交易记录支持情绪/原因标签并可统计
2. Phase B  
- 股票搜索支持代码/名称/拼音  
- 可导出分享卡片（图片）
3. Phase C  
- 新闻接口可稳定返回  
- 页面失败时有降级提示，不影响主交易流程

### 3.5 风险与约束

1. 直接复用前端组件会引入样式体系冲突，建议仅复用逻辑。
2. 外部数据源可能限流/字段变更，必须做缓存和异常回退。
3. 当前项目已有成熟交易引擎，整合时避免改动核心撮合与绩效统计链路。

## 4. 后续待办清单（不涉及本次改代码）

### P0（先做）

1. 统一运行数据源配置：`market_data_source` 设为 `tdx_then_akshare` 或 `akshare_only`。
2. 明确同步方式：AkShare 用 `sync_akshare_daily.py`；按钮接口使用前先安装 `baostock`。
3. 做一次最小验证：同步 5-10 只股票，打开 K 线，确认 `candles` 非空且日期连续。

### P1（本周建议）

1. 设计实时行情后端接口（先设计不改码）：`GET /api/market/quotes`、`GET /api/market/indices`。
2. 规定实时层边界：仅用于当日展示/盘中估值，不替代历史回测数据。
3. 规定缓存和回退：10-60 秒缓存，失败回退到本地最近收盘数据。

### P2（后续可做）

1. 名称兜底链路：TDX 名称 -> 本地静态库 -> 东方财富单次查询并缓存。
2. 迁移 `turteltrace` 的复盘模板、标签体系、分享能力到 AntD 风格页面。
3. 若需要全市场长期计算，规划 DuckDB/Parquet 数据层升级。

## 5. 本次决定

1. 本次不修改业务功能代码。
2. 先沉淀结论和执行清单，再按优先级分批落地。
