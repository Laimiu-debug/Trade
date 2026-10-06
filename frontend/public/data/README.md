# 离线证券搜索快照

`stock-database.slim.json` 随源码与发行包提供，仅用于代码、名称、拼音和行业检索。构建和运行不需要联网获取此文件。

本次快照于 2026-10-06 从 [BaoStock](https://www.baostock.com/) 的 `query_stock_basic()` 与 `query_stock_industry()` 获取，使用 SDK 0.9.4。它包含提供方返回的 5224 个在上市沪深 A 股证券；行业资料更新时间为 2026-10-05。覆盖范围取决于提供方，不含北交所，未收录代码仍可手工输入。

转换规则：

- 仅保留 `type=1`、`status=1`，且代码属于 `sh.6*`、`sz.0*`、`sz.3*` 的记录。
- `sh` 映射为 `SSE` / `.SH`，`sz` 映射为 `SZSE` / `.SZ`；证券代码保留前导零，按 `ts_code` 排序。
- 名称和行业保留提供方原值；`cnspell` 由 pypinyin 0.55.0 的 `FIRST_LETTER` 生成，转为小写；上市状态映射为 `L`。

`stock-library-source.json` 记录获取时间、查询方法、覆盖范围、记录数、行业更新时间、原始响应摘要和最终库的 SHA-256。该快照不包含行情价格或账户资料，也不用于推断历史时点的股票池。

更新时应重新获取两个来源、应用上述转换规则，并一起更新库与来源清单。提交前运行 `backend/tests/test_development_tools.py`，构建后运行 `frontend/scripts/smoke-packaged-rebuild.mjs`；发行包必须能够通过 `/data/stock-database.slim.json` 读取此资源。
