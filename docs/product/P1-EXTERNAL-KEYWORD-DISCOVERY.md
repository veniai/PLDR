# P1 外部关键词发现纵向切片

更新时间：2026-08-28

## 目标

在 P0.3 受控采集箱之上增加一个明确独立的公开资料发现入口。分析员输入关键词、选择新闻或一般公开网页范围，由现成外部检索后端返回真实结果；只有被勾选的结果才会抓取原始页面并进入既有待处理采集箱。搜索过程保留关键词、查询运行、检索渠道、结果和最终材料之间的追踪关系。

这不是工作台左侧的“搜索事件、实体、地点”筛选器。事件筛选器只作用于已入档事件；本入口发起外部检索，且在用户选择前不改变任何正式对象。

## 检索后端边界

| 组件 | 版本 | 许可证/条款 | 部署边界 |
| --- | --- | --- | --- |
| Brave Search API | REST v1（`POST /res/v1/{news或web}/search`） | Brave Search API Terms of Service | 外部 SaaS。部署方通过 `PLDR_SEARCH_API_KEY` 提供密钥；PLDR 不提交、记录或硬编码密钥。 |
| SearXNG | 2026.8.22 | AGPL-3.0-or-later | 运营方自行部署并启用 JSON 输出的实例，通过 `PLDR_SEARCH_BASE_URL` 配置。PLDR 仅通过 HTTP 调用，不复制或修改 SearXNG 源码。 |

默认提供 Brave 薄适配层；未配置密钥时 API 返回 503 和明确错误，界面显示未配置状态，绝不生成演示搜索结果。SearXNG 是可配置的替代后端，适合需要自管检索边界的部署。PLDR 不自研网页索引、通用搜索引擎或上游应用套件。

运行配置：

```env
PLDR_SEARCH_PROVIDER=brave
PLDR_SEARCH_BASE_URL=https://api.search.brave.com
PLDR_SEARCH_API_KEY=
PLDR_SEARCH_TIMEOUT_SECONDS=12
```

## 证据与对象边界

- `external_search_query_runs` 保存一次关键词查询、范围、渠道、状态、延迟和错误。
- `external_search_results` 保存规范化结果、原始 URL、站点、标题、摘要、可获得时间、排名和后端引擎标识。
- `external_search_selections` 用规范化 URL 指纹把一个已识别结果链接到至多一个采集箱条目，并记录尝试次数、状态和错误。
- `external_search_selection_events` 记录每一次提交、复用和重试所对应的查询运行与结果；采集条目详情显示最新 trace，并保留完整 `search_history`。
- 检索渠道不是正式 `Source`；搜索标题、摘要、排名、相对时间和模型答案都不是 `Evidence`。
- 未勾选结果不会发起原始页抓取，也不会产生采集箱条目。
- 勾选后仍沿用 P0.3：抓取原始 HTML，保存原始/提取快照，生成候选，人工确认后才创建或关联正式 `Source`、`Document`、`Snapshot`、`Event`、`Entity`、`Claim`、`Evidence`。
- 人工确认结果继续保留 intake 与 external search trace；正式 `Document.metadata` 通过 `intake_item_id` 可回到该采集条目和查询链。
- 重复提交同一规范化结果 URL 复用原采集箱条目；重复确认复用原正式处置结果，不产生重复有效对象。

## 安全与失败行为

1. 检索后端超时、限流、认证失败、返回非法 JSON 或不可达时，查询运行记录失败原因，API 返回可解释状态，界面显示错误；空结果保持真实空状态。
2. 结果 URL 只允许 HTTP/HTTPS 并做规范化。抓取阶段继续执行现有公共地址、每一跳重定向和 SSRF 校验；私网、重定向到私网或无正文页面只形成可重试失败采集项。
3. 后端返回的标题/摘要先剥离标记并做文本规范化；前端渲染统一转义，搜索文本不参与证据抽取。
4. 所有搜索、选择、抓取失败都不修改正式对象或报告。

## 验收矩阵

| 要求 | 自动验收 |
| --- | --- |
| Brave/SearXNG 薄适配层 endpoint、认证参数、范围参数、响应解析契约和超时映射 | `test_search_provider_adapters_call_real_backend_contracts` |
| 新闻与一般网页两个范围、结果规范化、渠道/URL/站点/标题/摘要/时间展示、空结果和后端失败不伪装 | `test_external_search_normalizes_scopes_and_does_not_fake_failures` |
| 只抓取选中项、未选项不产生条目、追踪关键词/查询/渠道/结果与跨查询重复选择历史，失败保留错误并可重试 | `test_selected_search_results_selectively_enter_intake_and_can_retry` |
| 搜索结果隔离于正式区，确认后证据精确回链原始快照，重复选择和重复确认幂等，报告不含搜索摘要 | `test_external_search_stays_evidence_first_and_idempotent_after_confirmation` |
| 私网 URL、重定向到私网、无正文、未配置后端等失败不影响正式对象 | `test_external_search_and_fetch_failures_leave_formal_area_unchanged` |
| 既有公共网页、RSS、粘贴文本、本地文件与 P0.3 审核闭环回归 | P0.3 原有测试保留并全部通过 |

项目原生验收命令：

```bash
./scripts/test-p0.sh
```

自动测试使用受控替身模拟检索后端和原始页，不访问实时外网。真实浏览器验收需为运行环境配置 Brave API 密钥或自有 SearXNG 实例。

## 2026-08-28 浏览器验收记录

使用本地运行的 SearXNG 2026.8.22 作为真实后端：

1. 新闻范围查询 `Ever Given Suez Canal` 返回 35 条真实结果；结果展示站点、`searxng:news` 渠道、原始 URL、标题和摘要。
2. 一般网页范围查询返回 20 条真实结果，渠道显示 `searxng:web`。
3. 无匹配关键词显示真实空状态；停止 SearXNG 后查询显示后端不可达错误，均未出现演示数据。
4. 选择 Reuters 结果因原始页 401 失败，错误和“重试抓取”入口保留；选择 Wikipedia 结果被现有 SSRF 防护拒绝并保留可解释错误。
5. 选择 BTS 结果成功进入采集箱；条目详情回看关键词、查询运行 ID、`searxng:web` 渠道、结果 ID、原始 URL、搜索摘要和原始/提取快照。
6. 人工确认后生成正式事件、文档、快照、主张和证据；证据快照显示 `<mark>` 高亮，来源类型为 `intake-search`，并回链 `https://www.bts.gov/data-spotlight/ever-given-suez-canal`。

## 非目标

- 不保存关键词后自动定时监控。
- 不自动提醒、自动抓取全部结果或无人值守入档。
- 不扩展通用固定来源、任务队列、网页版本历史、PostgreSQL/PostGIS 或 72 小时连续运行。
- 不接入深度研究 Agent、社交平台登录爬虫、自动事实裁决或自动报告结论。
