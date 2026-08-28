# ADR-0006：外部关键词发现使用薄检索适配层

日期：2026-08-28

状态：已接受

## 背景

P0.3 已经提供受控采集箱、AI 候选、证据定位和人工确认闭环，但分析员仍需要先知道目标 URL 才能导入材料。P1 第一个纵向切片需要关键词发现能力，覆盖新闻和一般公开网页。PLDR 的差异在于证据链和人工确认，不在于自建网页索引或复制 World Monitor、BettaFish、MiroFlow 等上游应用。

## 决策

新增独立外部搜索适配层，不自研索引：

1. 默认接入 Brave Search API REST v1，部署方通过环境变量提供密钥。
2. 支持将 `PLDR_SEARCH_PROVIDER` 配置为 `searxng`，调用运营方自管且启用 JSON 输出的 SearXNG 实例。
3. 查询运行和结果保存在独立的 `external_search_*` 表，不写入正式 Source、Document、Event、Claim、Evidence 或报告。
4. 只有分析员勾选结果后才调用既有公共网页抓取与 SSRF 防护，成功页面进入 P0.3 采集箱。
5. 用规范化 URL 指纹建立结果到采集条目的幂等选择关系，并用 intake review 保留查询、渠道、结果、关键词和原始 URL 追踪。

组件版本、条款和部署边界记录在 `docs/product/P1-EXTERNAL-KEYWORD-DISCOVERY.md`，并通过 `/pldr-api/v1/config` 暴露不含密钥的运行时元数据。

## 后果

- PLDR 可以复用真实检索能力，避免维护网页爬取索引、排名和搜索基础设施。
- 未配置 Brave 密钥或 SearXNG 地址时，功能显式失败；测试用受控替身保持离线可重复。
- 搜索结果只是线索。正式证据仍必须来自用户选择后成功抓取的原始页面快照，并经过候选与人工确认。
- Brave 的配额、可用性和条款由部署方承担；选择 SearXNG 的部署方需自行满足 AGPL 与实例运维要求。
