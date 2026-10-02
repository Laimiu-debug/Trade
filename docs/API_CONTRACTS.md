# API 契约生成与漂移检查

新应用 `/api/v1` 和 `/health` 的OpenAPI来自实际FastAPI路由与Pydantic schema。生成器不启动应用生命周期，不打开数据库、不执行迁移、不读取行情或联网。

```powershell
python scripts/generate_rebuild_contracts.py
python scripts/generate_rebuild_contracts.py --check
```

输出 `docs/api/openapi.generated.json` 与 `frontend/src/rebuild/api-contracts.generated.ts`，后者保存前者SHA-256。生成排序确定；CI及跨平台打包工作流运行 `--check`，路由、请求/响应模型或查询参数变化后必须重新生成并评审差异。

TypeScript导出 `Components`、`Operations`、`ApiRequestBody` 和 `ApiResponse`。会话连接以及实盘成交/资金表单使用生成契约。金额在接口模型中保持十进制字符串，日期为明确字符串格式；整数/范围限制仍由服务端校验，TypeScript `number` 不替代这些约束。

JSON Schema组合、引用、必填/可选、枚举、空值、数组、附加属性以及文件Blob均按实际schema映射。版本化研究结果中的开放字典保留 `unknown` / `Record<string, unknown>`，不凭生成器猜字段。页面的精细结果视图类型和运行时结果校验仍负责具体算法版本；尚未声明 `response_model` 的响应不能因“已生成契约”就宣称严格覆盖了所有字段。

更新方法：先改服务端schema/路由和对应回归，再运行生成器、审查JSON与TS差异、执行前端构建和相应API测试。生成文件不手工修改。
