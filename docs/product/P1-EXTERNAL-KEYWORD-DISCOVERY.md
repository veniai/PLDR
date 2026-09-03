# P1 外部关键词发现纵向切片

更新时间：2026-08-31

本文档记录关键词发现切片的具体契约和验收，不代表完整 P1。阶段级用户功能、性能指标、当前状态和退出条件以 `FUNCTIONAL-PERFORMANCE-REQUIREMENTS.md` 为准。

## 目标

在 P0.3 受控采集箱之上增加一个明确独立、归属于专题的公开资料发现入口。分析员输入关键词、选择新闻或一般公开网页范围，由现成外部检索后端返回真实结果；只有被勾选的结果才会异步逐条抓取原始页面，并进入该专题的待处理队列。搜索过程保留关键词、查询运行、检索渠道、结果、人工选择和最终材料之间的追踪关系。

这不是工作台左侧的“搜索事件、实体、地点”筛选器。事件筛选器只作用于已入档事件；本入口发起外部检索，且在用户选择前不改变任何正式对象。

从产品拓扑看，**关键词搜索与确定网页、后续 API 来源、粘贴文本和本地文件是并列输入**。已知可靠地址应直接导入或持续监测；关键词搜索负责补充尚不知道 URL 的材料，而不是所有采集的必经前置步骤。当前已实现网页、粘贴文本、本地文件、手动 RSS、固定网页监测和关键词搜索；通用 API 可靠来源仍属于完整 P1 的后续范围。

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

## 专题化查询工作区

1. 专题工作区发起的每次查询都进入一个明确专题、系统“待归类”专题或由用户同时创建的新专题；查询历史和已加载结果按专题隔离，关闭搜索窗口后可以重新打开。旧 API 调用方可以不传专题，但服务端会把该查询归入系统“未分类工作”，不会把它冒充成某个已有专题的历史。
2. 前端每次可请求 10 或 20 条并通过真实后端页继续加载。Brave 每页最多返回所选数量；SearXNG 的实际页大小由运营方实例决定，若返回更多结果，PLDR 会在单查询 100 条上限内保留完整运营方页面。上游相邻页面重叠时按规范化 URL 去重，结果排名按当前查询中首次出现的顺序重排。
3. 单次查询最多持久加载 100 条，单批最多选择 100 条；支持“全选本页”“全选已加载”和“取消选择”。这些是可控工作区上限，不代表上游只有 100 条。
4. 当前 Brave/SearXNG 适配都不承诺可信总数。因此 API 返回 `loaded_count`、`returned_count`、`has_more`、`next_cursor` 和 `total_known=false`；界面只说“已加载 N 条”以及是否还能继续，不拿页大小或当前加载量冒充总结果数。
5. `language=auto` 根据关键词选择检索语言：当前只要关键词含中文，就在 Brave 使用 `zh-hans`、在 SearXNG 使用 `zh-CN`；不会从字形自动判断繁简。用户手动指定 `zh-TW`、`zh-HK`、`zh-Hant` 等繁体区域或脚本时，Brave 映射为 `zh-hant`，SearXNG 映射为 `zh-TW`。
6. 提交已选择结果后立即返回 `202 Accepted` 和逐条任务。专题页持续显示 `queued`、`fetching`、`generating`、`ready`、`failed`；一条失败不会取消同批其他结果，也不要求用户等待整批同步完成。

工作台的“今天”页按待审核、处理中、失败和已确认事件组织注意力；“发现资料”保留搜索产生的任务；“待审核”是正式入档前的操作入口。它们读取同一专题的持久状态，不用前端临时成功提示替代服务端结果。

## 证据与对象边界

- `external_search_query_runs` 保存一次关键词查询、范围、渠道、状态、延迟和错误。
- `external_search_results` 保存规范化结果、原始 URL、站点、标题、摘要、可获得时间、来源页、排名和后端引擎标识；跨页重复 URL 不会产生另一个当前查询结果。
- `external_search_selections` 用规范化 URL 指纹把一个已识别结果链接到至多一个采集箱条目，并记录尝试次数、状态和错误。
- `external_search_selection_events` 记录每一次提交、复用和重试所对应的查询运行与结果；采集条目详情显示最新 trace，并保留完整 `search_history`。
- 检索渠道不是正式 `Source`；搜索标题、摘要、排名、相对时间和模型答案都不是 `Evidence`。
- 未勾选结果不会发起原始页抓取，也不会产生采集箱条目。
- 勾选后仍沿用 P0.3：逐条抓取原始 HTML，保存原始/提取快照，生成候选。审核默认只允许合并到当前专题对象；跨专题复用必须显式开启并展示目标归属。人工确认后才创建或关联正式 `Source`、`Document`、`Snapshot`、`Event`、`Entity`、`Claim`、`Evidence`。
- 确认预览以语义对象展示将写入的 Source、Document、Snapshot、Event、Claim、Evidence、关系和动作，而不是让分析员直接判断原始 JSON。确认响应返回正式事件 ID/档案入口和下一条待审任务。
- 人工确认结果继续保留 intake 与 external search trace；正式 `Document.metadata` 通过 `intake_item_id` 可回到该采集条目和查询链。
- 重复提交同一规范化结果 URL 复用原采集箱条目；重复确认复用原正式处置结果，不产生重复有效对象。

## 安全与失败行为

1. 检索后端超时、限流、认证失败、返回非法 JSON 或不可达时，查询运行持久记录结构化错误：错误码、阶段、摘要、影响、是否可重试、建议动作、技术信息、追踪 ID、上游状态和可获得的 `Retry-After`。空结果保持真实空状态。
2. 结果 URL 只允许 HTTP/HTTPS 并做规范化。抓取阶段继续执行 DNS 解析、公共地址和每一跳重定向 SSRF 校验，并默认拒绝不确定或不安全地址。超时、限流等暂时性失败可重试；DNS/SSRF 策略拒绝标为不可直接重试，必须先更换公开地址或修正部署网络，不能用按钮反复绕过策略。
3. 后端返回的标题/摘要先剥离标记并做文本规范化；前端渲染统一转义，搜索文本不参与证据抽取。
4. 模型超时或非法输出时保留原文并显示可重试失败，不用规则摘录冒充成功的 AI 候选；重新分析不重复抓取。仅在没有配置模型时才明确使用确定性规则模式。无法形成可定位证据的条目不得确认。
5. 所有搜索、选择、抓取和候选生成失败都不修改正式对象、态势指标或报告。

## 验收矩阵

| 要求 | 自动验收 |
| --- | --- |
| Brave/SearXNG 薄适配层 endpoint、认证参数、范围参数、响应解析契约和超时映射 | `test_search_provider_adapters_call_real_backend_contracts` |
| 新闻与一般网页两个范围、结果规范化、渠道/URL/站点/标题/摘要/时间展示、空结果和后端失败不伪装 | `test_external_search_normalizes_scopes_and_does_not_fake_failures` |
| 只抓取选中项、未选项不产生条目、追踪关键词/查询/渠道/结果与跨查询重复选择历史，失败保留错误并可重试 | `test_selected_search_results_selectively_enter_intake_and_can_retry` |
| 搜索结果隔离于正式区，确认后证据精确回链原始快照，重复选择和重复确认幂等，报告不含搜索摘要 | `test_external_search_stays_evidence_first_and_idempotent_after_confirmation` |
| 私网 URL、重定向到私网、无正文、未配置后端等失败不影响正式对象 | `test_external_search_and_fetch_failures_leave_formal_area_unchanged` |
| 同一专题查询真实加载三页、跨页 URL 去重、重开历史、未知总数和一次选择 25 条 | `test_three_pages_persist_deduplicate_reopen_and_select_25` |
| 结构化限流错误持久保存并可按追踪 ID 重开；首次失败可在同一查询运行重试 | `test_search_error_is_structured_and_persisted`、`test_failed_first_page_can_retry_same_run` |
| 查询和选择状态按专题隔离；降级候选与 DNS 策略拒绝给出正确重试语义 | `test_selection_state_and_result_ids_do_not_cross_topics` |
| SearXNG 运营方返回超过页面大小时不被适配层提前截断；旧搜索表增量、幂等迁移 | `test_searxng_operator_page_is_not_truncated_or_stranded`、`test_additive_migration_upgrades_original_search_tables` |
| 专题审核目标默认隔离、跨专题复用显式授权、语义预览、确认后正式事件与下一任务 | `test_topic_queue_and_merge_targets_are_strict_unless_reuse_is_explicit`、`test_semantic_preview_and_confirmation_return_navigation_and_next_task` |
| 抓取/模型错误分类、DNS fail-closed 与历史候选失败可恢复 | `test_error_contract_and_legacy_generation_failure_remain_reviewable` |
| 既有公共网页、RSS、粘贴文本、本地文件与 P0.3 审核闭环回归 | P0.3 原有测试保留并全部通过 |

项目原生验收命令：

```bash
./scripts/test-p0.sh
docker compose config --quiet
git diff --check
```

自动测试使用受控替身模拟检索后端和原始页，不访问实时外网。真实浏览器验收需为运行环境配置 Brave API 密钥或自有 SearXNG 实例。

浏览器验收至少覆盖两种视口，并逐项观察：专题创建/进入、查询历史重开、三页累计与全选已加载、异步任务状态、一条可恢复错误、一条 DNS/SSRF 不可直接重试错误、降级候选提示、语义预览、确认后打开正式事件和生成专题报告。真实外网结果会随时间变化，因此验收判断交互与证据边界，不把某个固定结果数量作为成功条件。

## 2026-08-28 浏览器验收记录（改版前历史证据）

使用本地运行的 SearXNG 2026.8.22 作为真实后端：

1. 新闻范围查询 `Ever Given Suez Canal` 返回 35 条真实结果；结果展示站点、`searxng:news` 渠道、原始 URL、标题和摘要。
2. 一般网页范围查询返回 20 条真实结果，渠道显示 `searxng:web`。
3. 无匹配关键词显示真实空状态；停止 SearXNG 后查询显示后端不可达错误，均未出现演示数据。
4. 选择 Reuters 结果因原始页 401 失败，错误和“重试抓取”入口保留；选择 Wikipedia 结果被现有 SSRF 防护拒绝并保留可解释错误。
5. 选择 BTS 结果成功进入采集箱；条目详情回看关键词、查询运行 ID、`searxng:web` 渠道、结果 ID、原始 URL、搜索摘要和原始/提取快照。
6. 人工确认后生成正式事件、文档、快照、主张和证据；证据快照显示 `<mark>` 高亮，来源类型为 `intake-search`，并回链 `https://www.bts.gov/data-spotlight/ever-given-suez-canal`。

这次记录证明原始纵向闭环，但不证明 2026-08-30 新增的专题查询历史、多页/批量选择、结构化错误和专题语义审核。后者必须按上面的当前验收矩阵重新执行，不沿用旧截图或旧结果数量冒充新证据。

## 参考项目的吸收边界

- 学习 Palantir 的对象集合、受控批量动作和动作追踪，但 PLDR 的正式对象仍服从证据优先链。
- 学习 OpenAleph 的调查内检索/筛选与查询上下文，World Monitor 的来源健康/新鲜度，OpenCTI 对可靠性与置信度的分离，changedetection.io 的运行历史/变化/重试，以及 NewsNow 的高密度可读列表。
- 这些都是交互和架构模式参考。PLDR 当前工作区为独立实现，不复制 World Monitor、SearXNG 或其他 AGPL/GPL 项目的受限源码；如未来正式引入上游代码或服务，必须单独记录固定版本、许可证、部署和数据条款。

## 非目标

- 不保存关键词后自动定时监控。
- 不自动提醒、自动抓取全部结果或无人值守入档。
- 不声称获取了准确总结果数；单次查询最多加载 100 条也不是搜索引擎规模声明。
- 不把当前关键词搜索切片描述成通用 API 可靠采集；API、RSS 和浏览器类持续来源仍属于完整 P1。
- 不把已有固定网页首切片、SQLite 和单 worker 描述为 PostgreSQL/PostGIS、30–50 个来源或 72 小时连续运行已经完成。
- 不接入深度研究 Agent、社交平台登录爬虫、自动事实裁决或自动报告结论。
