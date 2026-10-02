# 独立策略实验室

新入口是 `python scripts/trade_rebuild_lab.py`，使用 `trade_app`，不导入旧应用，也不读 pickle 缓存。混合波段和图形量价保持独立，不加入主策略列表。

## 可运行能力

- `freeze`：只读已经导入的新行情 manifest，生成可编辑的独立输入；校验以内容哈希命名的行情文件。
- `scan`：指定日期的可得数据筛选，保留每只股票的指标、打分、排名、筛除原因和缺数状态。
- `morning`：相同决策时点的晨报，输出 JSON 和离线 HTML。
- `diagnose`：指定日期的完整指标诊断；切换 `as_of_date` 可以复核历史目标日。
- `backtest`：共享新组合执行引擎的固定样本回测，包括整手、T+1、费用、滑点、保守双触发处理、部分退出与期末持仓估值。
- `optimize`：最多32个明确参数组或策略对照，每组保存完整输入与逐日证据，按完整波段目标次数、总收益、回撤、原序号排序。
- `presets` / `adopt-preset`：列出原晨报四个固定 pipeline，显式采用为新输入，保存预设版本、摘要与已接受的执行差异。
- `target-fit`：明确人工目标日期和候选参数，按旧唯科自定义谓词及评分计算可解释的样本内目标拟合；不修改生产策略或历史信号。
- `verify-rhythm`：独立的旧唯科规则验证 profile，输出 WANT 覆盖、REJECT 误报、额外命中、生产节奏波与紫转黄逐日对照；不能与拟合谓词混称。
- `validate-chart`：新因果回测的完整周期按原图形分数区间与 C/B/A 等级分桶，输出样本内胜率/PF/平均净收益与有样本门槛的单调检查。
- `diagnose-position`：相同参数下对照静态 Step1 池和卖出后刷新池，输出完整成员、成交、决策、资金、持仓和条件计划。

策略编号支持 `hybrid_band_v1`、`chart_volume_swing_v1`、`limit_up_arb_v1`、`ths_main_force_flip_v1`、`ths_main_force_golden_cross_v1`、`ths_force_rhythm_v1`。传统策略复用应用参数校验和计算。图形专用过滤条件不能用于传统策略。

## 使用

```powershell
python scripts/trade_rebuild_lab.py freeze --manifest "<数据目录>/market/<哈希>.json" --output sample.json
python scripts/trade_rebuild_lab.py morning --input sample.json --output experiments/morning-001
python scripts/trade_rebuild_lab.py backtest --input sample.json --output experiments/backtest-001 --checkpoint-limit 10
python scripts/trade_rebuild_lab.py backtest --input sample.json --output experiments/backtest-001 --resume
python scripts/trade_rebuild_lab.py optimize --input sample.json --output experiments/optimization-001
python scripts/trade_rebuild_lab.py presets
python scripts/trade_rebuild_lab.py adopt-preset --input sample.json --output compound.json --preset hybrid_compound --accept-adapted
python scripts/trade_rebuild_lab.py backtest --input compound.json --output experiments/compound-001
python scripts/trade_rebuild_lab.py target-fit --input target-input.json --output experiments/target-001
python scripts/trade_rebuild_lab.py diagnose-position --input step1-input.json --output experiments/position-001
python scripts/trade_rebuild_lab.py verify-rhythm --input verify-input.json --output experiments/verify-001
python scripts/trade_rebuild_lab.py validate-chart --input chart-input.json --output experiments/chart-validation-001
```

每个新任务需要新的输出目录。`--resume` 要求原输入、代码与资源预算一致。Ctrl+C、超时和失败保留已经提交的5日期检查点；不接受修改参数后接着写同一实验。恢复逐个校验输入摘要、前序摘要、日期和检查点内容。并发运行同一目录由操作系统文件锁阻止。已有输入文件不会被覆盖。

通达信/在线历史导入通常缺少真实历史可得时间。默认严格模式将其排除；如要做回顾性研究，显式设置 `config.execution_strict=false`。晨报会标记假设历史资料在本地收盘后可见，不补造 available_at，已知迟到数据仍然排除。

输入格式为 `trade-lab-input-v1`，字段包括：

| 字段 | 含义 |
|---|---|
| `datasets` | 1至64个证券，每份包含 `symbol`、`bars`、可选 `source_id` 和 `bars_sha256` |
| `bars` | 按日递增的 OHLC、整数 volume、可选 amount、带时区的 available_at；每个证券80至2000根 |
| `strategy_id`、`params` | 策略及实际使用参数；未知字段拒绝 |
| `filters` | 20日成交额、40日涨幅范围、样本内趋势排名、每日名额；独立图形策略另支持确认类型、等级、量价比和可选排名加分 |
| `config` | 与组合工作台相同的资金、仓位、池刷新、退出、费用和滑点参数 |
| `start_date`、`end_date`、`as_of_date` | 回测起止、筛选/晨报/诊断日期，均为 YYYY-MM-DD |
| `window_sample_days`、`target` | 非重叠波段统计窗口及目标收益比例，默认20个样本日期和0.28 |
| `variants` | 参数组列表，每项可覆盖 strategy_id/params/filters/config；更换策略时重新取该策略默认参数 |
| `preset_adoption` | 采用工具写入的预设编号、版本、摘要、差异接受记录；实际计算以完整输入参数为准 |
| `target_fit` | 目标证券、观察起止、人工目标日期及1至32个独立拟合参数组 |
| `rhythm_verification` | 固定验证 profile 的证券、观察起止、WANT/REJECT 日期；没有可执行参数覆盖 |
| `step1` | 实际四步漏斗的 Step1 参数、流通股本股数及来源日期、关注证券列表 |

例如，冻结后将以下配置合并到输入，可比较两种同花顺信号：

```json
{
  "strategy_id": "ths_main_force_flip_v1",
  "params": {},
  "variants": [
    {"strategy_id": "ths_main_force_flip_v1"},
    {"strategy_id": "ths_main_force_golden_cross_v1"}
  ]
}
```

每组结果在 `variant-NNN/` 保存检查点和摘要。根目录 `manifest.json` 含完整行情、参数、代码摘要和预算；`result.json` 含比较结果；`report.html` 仅渲染转义后的数据，不执行导入 HTML。

## 四个固定晨报预设

`hybrid85_fast_3slot`、`hybrid_dual_30`、`hybrid88_ths_2slot`、`hybrid_compound` 的原始常量逐项与旧脚本校验。预设名包含88的第三项原实际最低分为82，保留真实常量，不从名称猜参数。筛选门槛、THS/节奏/来源、每日名额、持仓数、止盈止损、最长持有、移动退出激活、时间退出、保本激活全部映射到实际执行参数；图形/混合策略还冻结入场箱顶，支持箱体结构止损与 MA20 箱体破坏退出。

采用必须提供 `--accept-adapted`，因为执行语义有意修正：已知日线收盘条件在下一观察日开盘行动；已知峰值激活保护，不以当根后来高点解释更早低点；跳空使用实际开盘价。共享现金、整手、T+1、费用、滑点与仓位比例取代旧独立槽位复利。旧未来去重、重复趋势加5分、缓存信号排名和重叠窗口不能被称作相同历史执行。预设记录 `exact_legacy_equivalence=false`，任何后续手工改动以冻结 manifest 为准。

## 人工目标拟合

示例字段合并到已有行情输入：

```json
{
  "target_fit": {
    "symbol": "sz301326",
    "date_from": "2026-04-01",
    "date_to": "2026-06-03",
    "target_dates": ["2026-04-08", "2026-04-30", "2026-06-02"],
    "variants": [{}, {"deep_trough_percentile_max": 0.04}]
  }
}
```

使用独立 `legacy-weike-custom-target-fit-v1` 谓词，明确保留旧自定义脚本与生产节奏规则的差异：深谷可为平态且不要求生产连跌天数；翻转区间默认0.30至0.55，读取 THS 的前态；散户过滤默认阈值0.25。未实际应用的生产参数拒绝。逐日期计算只读当时可得前缀，人工标签不进入指标或信号谓词。

目标全部命中的候选按旧分数 `信号总数 + 2×额外紫转黄信号 + 其他额外信号` 升序，再按输入序号排序；若无完全命中，仅按目标命中数降序、序号排序。每组保存全部日期门槛、命中、漏报和额外信号。目标日证据不可观察时不选最佳参数，不把缺数当作无信号。最多100个目标日期、366个观察日期、32个显式候选；不会将旧972组网格静默截成32组。结果始终标记 `supervised_in_sample_target_fit_only`，不自动采用参数，不声称样本外效果。

## 独立节奏规则验证与逐日对照

`verify-rhythm` 使用 `legacy-weike-rule-verification-v1`，逐项对应原 `verify_weike_rules.py::should_buy`。该第二 profile 的翻转分位为0.35至0.52，谷底转折必须在0.17至0.30，深谷不高于0.05且必须下降态；先检查波形、0.72顶部拒绝与0.22散户过滤。这与旧 `tune_weike_rhythm.py` 的自定义拟合谓词、生产 `ths_force_rhythm_v1` 都不同，三者没有别名替换。

```json
{
  "rhythm_verification": {
    "symbol": "sz301326",
    "date_from": "2026-03-01",
    "date_to": "2026-06-04",
    "want_dates": ["2026-04-08", "2026-04-28", "2026-04-30", "2026-06-02"],
    "reject_dates": ["2026-03-04", "2026-03-16", "2026-03-24", "2026-04-01", "2026-04-15", "2026-05-14", "2026-05-20", "2026-06-04"]
  }
}
```

WANT与REJECT各最多100日，要求属于已提供冻结观察区间且互不重叠。标签只作用于结果归类。输出每一天的指标、THS、逐门槛与拒绝原因，WANT覆盖/漏报、REJECT误报、额外命中、不可观察日期独立列出；无法观察的覆盖值为null。还记录生产节奏波信号日期、紫转黄日期、目标与紫转黄详情及生产目标覆盖，承接原 `debug_weike_rhythm.py` 的逐日前缀比较。不会写入生产策略、研究运行或交易草稿。

## 图形评分与完整周期目标统计

`validate-chart` 仅接受 `chart_volume_swing_v1`，复用同一冻结输入、5日计算检查点和恢复流程。在完整清仓周期上使用入场时已保存评分与等级，按 `[0,62)`、`[62,70)`、`[70,75)`、`[75,82)`、`[82,101)` 和 C/B/A 分桶。各桶输出完整周期数、胜率、平均净收益比例、收益比例正负和的PF、止盈比例、28%/25%目标次数与标的日线持有间隔。

部分退出腿先合并，开放周期与缺少入场评分的周期单列。每个可比桶至少20个完整周期，至少两个有效桶才判断单调性；不足返回未知。没有亏损时PF为null，不沿用旧99；未知分值不补0。完整周期、前30高分样本、达28%周期均可复核。旧未来15日最高分去重被明确停用，不输出伪造的去重前后可执行绩效。

每个 `backtest` / `optimize` / `validate-chart` 参数组同时输出 `trade_statistics`：完整周期目标次数、快目标次数、完整与开放周期及费用后收益。默认目标0.28、快窗口20个标的日线间隔，保存实际可配置值。原名称 `fast_30` 的实际门槛是28%，新名称避免误解。入场成本按共享账本的分位四舍五入并计费用；未清仓不会为了统计被假设卖出。组合资金曲线和周期平均收益分别展示，重叠周期收益不相乘。

## Step1 与持仓刷新诊断

```json
{
  "step1": {
    "parameters": {"amount_threshold": 100000000, "amplitude_threshold": 0.01},
    "float_shares": [{"symbol": "sh600000", "value": 100000000, "as_of_date": "2024-01-01"}],
    "focus_symbols": ["sh600000"]
  }
}
```

股本单位为股，成交额为元，比例为小数。使用实际新 Step1 的40日收益口径，至少251根当时可见日线；未来股本、严格模式未知日期股本、短历史明确排除。两个路径使用同一策略、门槛和资金配置；静态池在首个执行日冻结，持仓池在真实卖出后的下一观察日刷新。每日前K在成员资格后作用，执行端还保留更严格的 `entry_top_k`。

`static/` 与 `position/` 分别保存可恢复检查点。结果包含每只证券的 Step1 指标与拒绝原因、两池成员、完整成交和权益、关注股票成交、期末现金/持仓/待买，以及下次观察的条件 plan/open 列表。未知的下一开盘价与股数均为null。当日原始信号、当前池筛选名额与静态池待执行候选分开显示，不把日终信号解释为当日开盘成交。不伪造旧 RUN_ID，也不沿用旧诊断中60/55分门槛混用作为同口径对照。

## 有意修正的旧脚本行为

1. 股票池是明确提供的历史样本，不声称具备历史全市场成员信息。排名并列按规范证券代码稳定排序。
2. 回测在次日开盘前过滤可得历史，晨报以北京时间当日结束为界；未来尾部价格或迟到数据不能进入当时信号。
3. 成交额缺失保留为未知；要求成交额门槛时明确排除，不补为0。
4. 默认不加样本排名奖励；如启用，记录 `rank_bonus_enabled` 和样本范围。
5. 不使用“未来15天最高分信号替换更早信号”的事后去重。此旧方法可以描述回顾性样本，但不能作为当时可执行信号。
6. 波段统计使用真实连续资金曲线，下一波段以前一段期末资产为分母。最后不足窗口的区间展示但不计入完整波段命中次数。
7. 优化排序是样本内比较；目标数字是统计条件，不是盈利承诺。样本外验收使用专门 Walk-forward 工作流。

## 旧脚本去向与剩余差异

| 旧脚本 | 新入口 / 验收范围 |
|---|---|
| hybrid_band_screener.py | `scan`，支持相同图形/THS/节奏评分；输入改为冻结 manifest，未做直接全市场TDX自动采集 |
| hybrid_band_optimizer.py、optimize_band_target.py | `optimize`，显式参数组、组合波段统计及有版本的因果退出；完整大网格须显式分实验规划，不静默截断 |
| morning_band_report.py | `presets` / `adopt-preset` / `morning` / `backtest`，四个真实参数预设与执行差异同时保存，不加载pickle |
| backtest_band_rotation.py、backtest_chart_volume_swing.py | `backtest`，共享组合执行；原事后最优去重明确停用 |
| backtest_limit_up_arb.py、backtest_limit_up_arb_step1.py | `strategy_id=limit_up_arb_v1` 的 `backtest`，可提供 `step1` 冻结股本/门槛并按配置刷新；未冒充历史全市场 |
| compare_ths_strategies_backtest.py | `optimize` 的 strategy_id 参数组，保存每组原始资金曲线和成交 |
| tune_weike_rhythm.py | `target-fit`：独立旧自定义谓词/人工目标评分；显式候选，不执行旧972组隐式全网格 |
| verify_weike_rules.py | `verify-rhythm`：第二固定验证profile，旧should_buy前缀/边界对照、WANT/REJECT/Extra与未知证据 |
| debug_weike_rhythm.py | `verify-rhythm`的生产节奏波/紫转黄逐日快照、日期与目标对照，另可用生产 `diagnose` 单日深查 |
| diag_position_step1.py | `diagnose-position`：固定样本、真实Step1、同参数静态/持仓刷新两条因果回放及结构化明细 |
| validate_chart_volume_scoring.py | `validate-chart`：完整因果周期的原分桶/等级、胜率/PF/平均收益、带最少样本要求的单调性验证；保留完整周期供追溯 |
| sync_akshare_daily.py、sync_baostock_daily.py | 数据同步模块；独立同步 CLI 和服务器文件传送属 F06，不能以本实验室代替 |

## 资源与证据

最多60000根总日线，回测日期最多366个样本日期，参数组×日期×证券最多50000。每5日期计算子进程120秒/512MiB，输入32MiB、单批输出4MiB，总检查点128MiB；超过预算返回明确错误。

`backend/tests/test_strategy_lab.py`、`test_strategy_lab_extended.py` 与 `test_portfolio_band_exits.py` 验证新旧图形/混合公式、旧四预设常量、旧自定义拟合谓词边界、标签不改信号、未来/迟到隔离、结构化两池回放、名额顺序、严格整数成交量、真实采用预设后的子进程成交与时间退出、摘要链恢复，以及日线高低不确定性。旧打印排版、pickle缓存和非因果成交不作格式或算法等价承诺。

2026-09-26补充验收：`test_lab_rule_verification.py` 比较旧固定should_buy全部阈值分支及真实逐日前缀指标，覆盖标签不改信号、未来/迟到隔离和真实CLI；`test_lab_score_validation.py` 覆盖原分桶边界、部分退出/未平仓、原比例PF公式、无亏损和少样本未知、精确费用成本门槛、真实进程暂停恢复。四个lab测试文件合计39项通过，架构边界检查通过。该记录不替代发布时根任务最终全套门禁。
