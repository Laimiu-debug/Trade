# Trade 设计系统 · Bright Workspace v2

更新：2026-10-06。风格：清爽浅色工作台——白色面板、柔和阴影、蓝色操作，彩色图标底块区分模块；红涨绿跌。

## 设计方向

浅灰画布承托白色卡片，卡片使用细边框与双层柔和阴影，悬停时抬升。每个导航模块有固定色调（蓝/绿/琥珀/紫/青/粉），页头、指标卡和卡片标题用同色调图标底块标识，便于快速定位。蓝色只用于操作、选中与品牌；状态和行情颜色语义独立。

## 三层令牌

`docs/design-tokens.json`（schemaVersion 2）是唯一来源：

1. **基础色板 `palette`**：neutral / blue / red / green / amber / violet / teal / pink 色阶（`d*` 为深色主题专用阶）。生成 `--palette-<色系>-<阶>`，仅供语义层引用，组件不直接使用。
2. **语义角色 `themes.<light|dark>.color`**：值写作 `{palette.blue.600}` 引用。角色名与 v1 兼容（`bg.surface`、`action.primary`、`market.up` 等），新增 `bg.muted`、`border.default/strong`、`action.subtle`、`tone.*`、`nav.activeText`；阴影新增 `xs` 与 `raised`。
3. **尺寸与组件 `font / spacePx / radiusPx / sizePx`**：字号增加 `micro/title`，圆角 6/8/12/14/18，侧栏 248、顶栏 60，新增 `iconSmall/iconTile/iconTileLarge`。

`scripts/design_tokens.py` 解析引用；`python scripts/generate_rebuild_tokens.py` 输出：

- `frontend/src/rebuild/tokens.generated.css`
- `frontend/src/shared/theme/design-tokens.generated.ts`（已解析的实际值，图表与 Canvas 直接使用）
- `journal-frontend/src/tokens.generated.css`
- `journal-frontend/src/design-tokens.generated.ts`

生成文件禁止手工修改。`--check` 只比较不重写。`python scripts/check_design_tokens.py` 对明暗两套主题执行 58 项 WCAG 对比度检查（正文 4.5、控件边框 3，含全部 `tone.*` 与导航选中态）。

## 图标

使用 `lucide-react`（ISC）。`frontend/src/rebuild/workspace-icons.tsx` 按**角色名**导出：

- `<Icon name="save" />`：按钮、标题内联图标，`aria-hidden`，不改变可访问名称。
- `<IconTile name="overview" tone="blue" size="sm|md|lg" />`：彩色底块，用于页头、指标卡。
- `WorkspaceIcon` 保留给导航旧调用。

页面不直接引用 lucide 组件；新增角色在 `icons` 映射中登记。卡片标题使用 `<h2 className="title-with-icon"><Icon name="…" />标题</h2>`。

## 色彩（浅色 / 深色）

| 角色 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| bg.canvas | #f4f6fa | #0b111c | 整体画布 |
| bg.surface | #ffffff | #151d2d | 卡片、菜单、弹层 |
| bg.subtle | #fafbfd | #1a2336 | 表头、次级区块 |
| bg.selected | #eef4ff | #1d3561 | 导航、选中状态 |
| border.subtle | #e3e8f0 | #283349 | 分隔、卡片边界 |
| border.control | #7f8ca1 | #6c7a92 | 输入框边界 |
| text.primary | #141b29 | #e7ecf4 | 标题、正文、数据 |
| text.secondary | #4a5568 | #b6c1d2 | 辅助说明 |
| text.muted | #5f6b80 | #93a1b7 | 日期、单位、元信息 |
| action.primary | #2563eb | #6b9bff | 主按钮、链接 |
| market.up | #d0313f | #ffa3ab | 上涨、正收益 |
| market.down | #0d7f5c | #74dcb1 | 下跌、负收益 |

状态使用 `status.success / warning / danger / info`，行情使用 `market.up / down / flat`，模块色调使用 `tone.*`。即使值相同也引用不同语义。所有状态有文字或形状提示，不能仅依靠颜色。

品牌源图为 `frontend/public/trade-mark.svg`：蓝色圆角底、白色 T 和行情柱。复盘模块使用同一 SVG。PNG、Apple 图标和多尺寸 ICO 由 `npm run icons:rebuild` 从源 SVG 渲染。

## 字体与尺寸

使用本机字体栈 Inter、Segoe UI、苹方、微软雅黑和 system-ui，不依赖远程字体请求。

| 项目 | 默认值 |
|---|---|
| 正文 / 表格 | 14px / 13px |
| 页面标题 / 核心指标 | 26px / 28px |
| 卡片标题 / 字段标签 | 16px / 13px |
| 桌面 / 移动页面留白 | 32px / 16px |
| 卡片内边距 | 24px，手机 16px |
| 常驻侧栏 / 顶栏（吸顶） | 248px / 60px |
| 表格行高 | 舒适 42px，紧凑 34px |
| 常规输入 / 按钮 | 36px |
| 控件 / 卡片 / 弹窗圆角 | 8px / 14px / 18px |
| 图标 / 图标底块 | 18px（按钮 16px）/ 32·36·44px |

金额、百分比与指标使用等宽数字。新版列表默认紧凑，显式保存过的舒适偏好继续生效。正文行高保持可读，不将阅读页压成数据表格。

## 页面与组件

- 导航使用线性图标、清晰分组和淡蓝选中态。侧栏与内容区分离，标题说明与主要操作对齐。
- 内容面板使用细边框和轻阴影，分区标题下有轻分隔；每个区域一个主要操作。
- 输入框、选择器、文本区、复选框和焦点状态统一；标签置于控件上方，单位与提示邻近字段。
- 表格表头使用浅底，数据行紧凑排列，悬停有反馈；宽表仅在自身容器滚动。
- 指标有标签、单位、数值和比较基准；缺失显示 `—` 或明确原因。
- 状态、加载、错误和空数据保留原有语义，错误不能伪装成空数据。
- 研究工作区沿用子导航、步骤、结果与历史结构，统一表单、选中状态与卡片；切换页面保留原有草稿行为。
- 事件规则编辑器使用 CSS 网格适应屏幕宽度，大量规则无需逐行订阅媒体查询；缩放和主题切换保持草稿与规则值。
- AI 抽屉使用同一背景、正文和操作角色，不再使用独立绿色玻璃背景。

## 主题与响应式

三套前端共享 `trade-theme-mode` 偏好。新版支持浅色、深色、跟随系统；旧复盘的 `lt-theme` 值作为兼容回退。复盘切换主题直接更新界面和图表，无需刷新页面，保留未保存输入。

新版在 700px 以下采用可横向滚动的主导航和单列内容；原工作台在 992px 以下使用导航抽屉；复盘模块在 900px 以下使用导航抽屉。宽表保持局部滚动；页面整体不产生横向溢出。

按钮、链接与字段有可见的键盘焦点。`prefers-reduced-motion` 关闭非必要过渡。主题切换时图表使用当前主题的实际颜色，价格上涨与下跌继续遵循红涨绿跌。

## 图表、打印和分享

净值曲线使用 `chart.series1`；均线使用 `chart.ma5 / ma10 / ma20`；网格、轴文字、成交量使用对应 chart 角色。tooltip 使用主题面板和正文颜色。专用指标/策略的颜色与符号继续表达业务分类。

打印保留现有 A4 模板和导出字段，用独立纸面样式保证中文长文、跨页表格、签名与图片可读。深色屏幕不改变白色纸面。PNG 分享卡使用浅色令牌的背景、正文和蓝色曲线。

## 验证

`python scripts/check_design_tokens.py` 检查浅深色角色集合与 40 个核心配对：普通文字、按钮、行情/状态标签至少 4.5:1，必要输入边界至少 3:1。此检查已接入 CI；它验证令牌配对，不等同于整站无障碍认证。

验收包括三套前端的生产构建、现有交互回归测试、新版真实/模拟账户导航及子页面、浅深色、键盘操作与窄屏溢出检查。实际截图用于检查桌面、平板和手机的视觉表现。

`frontend/e2e/event-layout.spec.ts` 覆盖 80 条可编辑规则在 1440 / 768 / 375px 和主题切换后的可用性与草稿保持。`workspaceTheme.test.tsx` 覆盖原工作台的主题持久化、跨工作空间更新与未保存输入保持。
