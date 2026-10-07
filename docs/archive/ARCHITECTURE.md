# Final Trade - 架构文档

## 概述

Final Trade 是一个全栈A股选股和模拟交易平台，采用现代化的模块化架构设计。

## 技术栈

### 后端
- **框架**: FastAPI (Python 3.11+)
- **数据源**: Baostock, AkShare, 通达信(TDX)本地文件
- **持久化**: JSON 文件存储
- **类型安全**: Pydantic 数据验证

### 前端
- **框架**: React 19 + TypeScript
- **构建工具**: Vite
- **UI 组件**: Ant Design
- **状态管理**: Zustand + React Query
- **图表**: ECharts

## 项目结构

```
final-trade/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── core/              # 核心业务逻辑
│   │   │   ├── signal_analyzer.py    # Wyckoff信号分析
│   │   │   ├── ai_analyzer.py        # AI股票分析
│   │   │   ├── screener.py           # 选股引擎
│   │   │   └── candle_analyzer.py    # K线分析工具
│   │   ├── providers/         # 数据提供者
│   │   │   ├── base.py              # 抽象接口
│   │   │   ├── tdx_provider.py      # TDX数据源
│   │   │   └── web_provider.py      # Web证据提供者
│   │   ├── utils/             # 工具模块
│   │   │   └── text_utils.py        # 文本处理工具
│   │   ├── config.py          # 配置管理
│   │   ├── state_manager.py   # 状态管理
│   │   ├── store.py           # 主存储类 (仍在优化中)
│   │   ├── models.py          # 数据模型
│   │   ├── sim_engine.py      # 交易模拟引擎
│   │   ├── tdx_loader.py      # TDX数据加载器
│   │   └── main.py            # FastAPI应用入口
│   └── tests/                 # 测试套件
│
└── frontend/                   # 前端应用
    └── src/
        ├── core/              # 核心页面
        ├── shared/            # 共享组件
        ├── state/             # 状态管理
        └── types/             # TypeScript类型
```

## 核心模块

### 1. 信号分析 (core/signal_analyzer.py)

**职责**: 检测Wyckoff累积和派发模式

**主要功能**:
- PS/SC/AR/ST/TSO/Spring/SOS/JOC/LPS 事件检测
- 阶段判断 (吸筹A-E, 派发A-E)
- 多维度评分 (阶段分数、事件强度、结构质量)
- 序列验证 (Wyckoff事件序列)

**关键方法**:
```python
SignalAnalyzer.calculate_wyckoff_snapshot(row, candles, window_days)
```

### 2. AI分析 (core/ai_analyzer.py)

**职责**: 集成AI服务进行股票分析

**主要功能**:
- AI提示词构建
- AI API调用 (支持多种提供者)
- 响应解析和验证
- 结论和置信度提取

**关键方法**:
```python
AIAnalyzer.analyze_stock(symbol, row, source_urls)
```

### 3. 选股引擎 (core/screener.py)

**职责**: 多步股票筛选算法

**筛选流程**:
1. **初始池过滤**: 按市场、换手率筛选
2. **技术指标分析**: 波动率过滤
3. **趋势分类**: A/A_B/B/C类评级
4. **风险评估**: 回撤幅度过滤

**关键方法**:
```python
ScreenerEngine.run_screener(params, input_pool)
ScreenerEngine.build_screener_result(symbol, as_of_date)
```

### 4. K线分析 (core/candle_analyzer.py)

**职责**: K线数据处理和分析工具

**主要功能**:
- 日期对齐和切片
- 突破候选检测
- 回撤分析
- 量价共振检测

**关键方法**:
```python
CandleAnalyzer.collect_volume_price_breakout_candidates(candles)
CandleAnalyzer.infer_recent_rebreakout_index(candles)
```

## 数据提供者架构

### 抽象接口 (providers/base.py)

```python
class MarketDataProvider(ABC):
    def get_candles(symbol, start_date, end_date) -> list[CandlePoint]
    def get_symbol_name(symbol) -> str | None
    def load_input_pool(markets, as_of_date) -> list[dict]

class AIProvider(ABC):
    def analyze_stock(symbol, context, provider_config) -> dict
    def test_provider(provider_config) -> dict
```

### 具体实现

- **TDXProvider**: 使用通达信本地文件
- **RSSWebEvidenceProvider**: 从RSS源收集新闻
- **SearchWebEvidenceProvider**: 使用搜索API收集证据

## 配置管理 (config.py)

**ConfigManager** 职责:
- 加载/保存配置到 `~/.tdx-trend/config.json`
- 配置验证 (ConfigValidator)
- 模拟交易配置管理
- AI提供者配置管理

**使用示例**:
```python
config_mgr = create_config_manager()
config = config_mgr.get_config()
config_mgr.set_config(new_config)
```

## 状态管理 (state_manager.py)

**StateManager** 职责:
- 选股运行记录
- 股票标注
- AI分析记录
- 模拟交易引擎状态

**持久化文件** (位于 `~/.tdx-trend/`):
- `screener_runs.json`
- `annotations.json`
- `ai_records.json`
- `sim_state.json`

**使用示例**:
```python
state_mgr = create_state_manager()
run = state_mgr.get_screener_run(run_id)
state_mgr.save_annotation(annotation)
```

## API端点架构

### 选股相关
```
POST /api/screener/run          # 运行选股
GET  /api/screener/runs/{run_id} # 获取选股结果
```

### 信号分析
```
GET /api/signals                # 获取Wyckoff信号
```

### 交易模拟
```
POST /api/sim/orders            # 创建订单
GET  /api/sim/orders            # 查询订单
GET  /api/sim/portfolio         # 持仓快照
POST /api/sim/settle            # 执行清算
```

### AI分析
```
POST /api/stocks/{symbol}/ai-analyze  # AI分析
GET  /api/ai/records                   # 获取AI记录
```

## 数据流

```
市场数据源 (TDX/AkShare)
    ↓
数据加载器 (tdx_loader.py)
    ↓
K线数据缓存
    ↓
┌─────────────────┬─────────────────┬─────────────────┐
│                 │                 │                 │
选股引擎        信号分析器      AI分析器
(screener)    (signal_analyzer) (ai_analyzer)
│                 │                 │
└─────────────────┴─────────────────┘
                    ↓
            状态管理器
        (state_manager.py)
                    ↓
            JSON持久化
```

## 设计原则

### 1. 单一职责原则 (SRP)
每个模块只负责一个明确的功能领域：
- `signal_analyzer` 只负责信号检测
- `screener` 只负责选股逻辑
- `candle_analyzer` 只负责K线分析

### 2. 开放封闭原则 (OCP)
通过抽象接口扩展功能：
- 新数据源: 实现 `MarketDataProvider`
- 新AI服务: 实现 `AIProvider`
- 新证据源: 实现 `WebEvidenceProvider`

### 3. 依赖注入
所有模块通过工厂函数创建，依赖通过构造函数注入：
```python
engine = create_screener_engine(
    candles_provider=self._ensure_candles,
    symbol_name_resolver=self._resolve_symbol_name,
)
```

### 4. 线程安全
- `StateManager` 使用 `RLock` 保护共享状态
- `InMemoryStore` 使用锁保护并发访问

## 重构历程

### 阶段 1: 基础模块提取
- 文本处理工具 (`utils/text_utils.py`)
- 信号分析器 (`core/signal_analyzer.py`)
- 数据提供者抽象 (`providers/`)

### 阶段 2: 配置和状态管理
- AI分析器 (`core/ai_analyzer.py`)
- Web证据提供者 (`providers/web_provider.py`)
- 配置管理 (`config.py`)
- 状态管理 (`state_manager.py`)

### 阶段 3: 业务逻辑模块化
- 选股引擎 (`core/screener.py`)
- K线分析器 (`core/candle_analyzer.py`)

**成果**:
- 原始 `InMemoryStore`: 3867行
- 当前 `InMemoryStore`: ~3100行
- **减少 20%**
- 新增模块: **3136+行**高质量、可维护代码

## 测试策略

### 单元测试 (计划中)
- 每个核心模块的独立测试
- Mock外部依赖
- 覆盖率目标: 80%+

### 集成测试
- API端点测试 (`tests/test_api.py`)
- 持久化测试 (`tests/test_store_persistence.py`)
- 数据同步测试 (`tests/test_market_data_sync.py`)

### E2E测试 (前端)
- Playwright测试用户流程
- 覆盖关键业务场景

## 性能考虑

### 优化策略
1. **数据缓存**: K线数据内存缓存
2. **惰性加载**: 按需加载K线数据
3. **分页处理**: 大数据集分页处理
4. **异步I/O**: FastAPI异步端点

### 监控指标
- API响应时间
- 内存使用
- 数据库查询性能
- 缓存命中率

## 安全考虑

### 已实现
- 路径遍历防护 (TDX加载器)
- API密钥管理
- 输入验证 (Pydantic)

### 待改进
- [ ] API限流
- [ ] CORS配置细化
- [ ] 敏感数据加密
- [ ] 审计日志

## 未来改进

### 短期 (1-2个月)
1. 完善单元测试覆盖
2. 添加API文档 (OpenAPI)
3. 实现数据层抽象 (Repository模式)
4. 优化前端代码结构

### 中期 (3-6个月)
1. 引入数据库 (PostgreSQL)
2. 实现后台任务队列 (Celery/RQ)
3. 添加用户认证和授权
4. 实现WebSocket实时推送

### 长期 (6个月+)
1. 微服务架构
2. 分布式缓存 (Redis)
3. 实时数据流处理
4. 机器学习模型集成

## 开发指南

### 添加新的数据源
1. 实现 `MarketDataProvider` 接口
2. 在 `store.py` 中注册
3. 添加配置选项
4. 编写测试

### 添加新的技术指标
1. 在 `candle_analyzer.py` 添加计算方法
2. 在 `screener.py` 中集成到筛选流程
3. 更新评分算法
4. 测试验证

### 添加新的AI提供者
1. 在 `config.py` 添加配置
2. 在 `ai_analyzer.py` 实现调用逻辑
3. 处理响应格式差异
4. 测试集成

## 相关文档

- [API文档](./API.md) - API端点详细说明
- [部署指南](./DEPLOYMENT.md) - 部署和运维
- [开发指南](./DEVELOPMENT.md) - 本地开发设置

## 联系方式

- 项目维护者: Laimiu
- 技术栈: Python 3.11+, FastAPI, React 19, TypeScript
- 许可证: MIT
