# ADR-0004 P0 采用 World Monitor 受控改造

状态：已接受

日期：2026-08-26

## 决定

P0 使用固定版本的 World Monitor 作为展示端底座，源码导入 `apps/dashboard/`。前三天优先复用现有 `osint-newsroom` 任务预设，并增加 `VITE_PLDR_MODE` 或等价运行时开关，收敛界面、数据入口和默认面板。P0 验收前暂不新增正式的第七个 `pldr` 站点变体。

PLDR 自建 `services/intel-api/`，采用 FastAPI 与 SQLite，负责来源、文档、网页快照、事件、实体、主张、证据、来源独立性、模型任务和报告。展示端只通过 `/pldr-api/v1/*` 消费这些对象。

## 原因

World Monitor 已具备 P0 最耗时的展示能力：MapLibre 与 deck.gl 地图、Panel 与 PanelLayoutManager、新闻事件簇、任务预设、刷新调度、来源分级、数据新鲜度、故障降级和信息缺口交互。

复用这些能力可以把 72 小时集中在 PLDR 的差异化闭环：多篇材料聚合成事件；关键主张连接真实原文证据；文档数量与独立来源数量分开；支持证据、冲突证据和信息缺口同时展示；报告从可追溯对象生成。

## 最可能的误判

换品牌、删面板和改接口无法自动得到 PLDR。World Monitor 的生产体系包含 Vercel、Railway、Redis、Convex、Tauri、计费和大量领域接口，其主要对象仍偏数据流、面板和地图标记。PLDR 的 Source、Document、Snapshot、Event、Claim、Evidence 必须独立建立。

新增正式站点变体会同时影响变体注册、面板注册、地图默认值、构建脚本、桌面端入口和测试契约。P0 先用任务预设和专用模式减少外围改动，验收后再决定是否建立正式变体。

## 直接复用

- Vite 与 TypeScript 应用壳。
- DeckGLMap、MapLibre 平面地图、地图聚类和点击交互。
- Panel、PanelLayoutManager、响应式布局和事件委托。
- `osint-newsroom` 任务预设及其面板收敛机制。
- SmartPollLoop、可见区域刷新、失败退避和缓存状态。
- 来源分级、数据新鲜度和 Intelligence Gap 交互思想。
- 基础主题、国际化、加载态、空状态和错误态。

## P0 改造

- `Live News` 数据入口改成 PLDR Event 流。
- `Threat Timeline` 改成专题事件时间线。
- `AI Insights` 改成结构化研判。
- `Latest Brief` 改成证据化简报。
- 地图点击进入 PLDR 事件详情。
- 新增证据抽屉、来源状态和独立来源指标。
- 增加 `/pldr-api` 开发代理与生产反向代理。

## P0 关闭

财经、加密、航班、船舶、能源、灾害等无关面板，以及 Pro、计费、账户、商城、Convex、Railway 中继、Tauri、MCP 和复杂风险算法均不进入 P0。代码可以暂时保留，默认界面和请求链路中必须关闭。

P0 默认只使用平面地图。3D 地球保留源码，不投入适配时间。

## 仓库布局

```text
PLDR/
├── apps/
│   └── dashboard/          # 固定版本 World Monitor 衍生前端
├── services/
│   └── intel-api/          # FastAPI + SQLite
├── packages/
│   └── contracts/          # OpenAPI、JSON Schema、共享类型
├── data/
│   └── demo/               # 公开专题和预计算降级数据
├── docs/
└── docker-compose.yml
```

导入时必须记录上游仓库、commit、日期和本地改动，保留许可证与版权声明。

## 许可证边界

World Monitor 当前使用 AGPL-3.0-only。P0 可以在遵守许可证的前提下进行研究和验证。长期私有源码、受限部署、对外服务或品牌使用需要正式复核许可证路径，并在继续派生、取得商业许可、重新实现自有展示端三条路线中选择。

上游各数据源还有独立的使用条款。P0 只使用 PLDR 自己登记的公开来源，不直接复制 World Monitor 的全量数据源目录。

## 72 小时顺序

第一天前半段固定上游 commit，跑通 dashboard，启用 `osint-newsroom`，增加 PLDR 模式并关闭无关请求。若 4 至 6 小时仍无法形成可控空壳，立即触发退出条件，保留 MapLibre 与 deck.gl 的交互参考，回到精简自建前端。

第一天下半段建立 FastAPI、SQLite、最小对象和固定演示 JSON，跑通地图、事件流和事件详情。

第二天完成 RSS 与网页采集、去重、事件聚合、主张证据、来源独立性和证据原文校验。

第三天完成研判、HTML 报告、来源健康、缓存降级、演示数据冻结和录屏。

## 退出条件

满足任一条件时，P1 转向自有展示端：

1. World Monitor 外围依赖持续阻碍 PLDR 页面和接口。
2. 接入 Event、Claim、Evidence 需要频繁改动 AppContext 与大量通用组件。
3. 删除无关请求与商业功能的成本高于重建精简界面。
4. 长期部署无法接受 AGPL 派生边界。

## 当前判断

这条路线完成 P0 的概率约为 75%，高于 72 小时内从零完成同等视觉质量的方案。World Monitor 应被限制在展示外壳范围，PLDR 的数据、证据和研判闭环必须保持独立。

## 实施补记，2026-08-26

P0 执行时触发了本 ADR 的退出条件。当前环境可以通过 GitHub 连接读取 World Monitor 的架构、任务预设和关键组件文件，但不能取得完整源码归档，也不能创建远端仓库。继续逐文件拼装完整上游会明显超过 P0 成本。

因此当前 `apps/dashboard/` 实现为 PLDR 自有轻量兼容壳，复现地图优先、Panel、事件流、时间线、来源状态和信息缺口等交互模式，未包含完整 World Monitor 源码。独立后端、API 契约和对象模型保持原决策不变。

该调整使 P0 达到可运行、可测试、可迁移状态。P1 在完整 checkout 可用后进行一次 overlay 试验，再根据改动范围和 AGPL 路径决定长期前端。
