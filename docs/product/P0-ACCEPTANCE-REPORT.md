# PLDR P0 验收报告

日期：2026-08-26

状态：通过

## 验收结论

P0 已形成可以直接启动的证据化开源情报纵向切片。系统在无模型 API、无实时外网数据的情况下仍能完整演示首页、事件、主张、证据、来源独立性、信息缺口和 HTML 报告。

当前版本用于验证产品闭环和数据约束。内置专题是人工整理、改写的苏伊士运河阻塞公开事件演示快照，进入业务使用前必须重新抓取并核验原始网页。

## 实现规模

| 对象 | 数量 |
|---|---:|
| Source | 16 |
| Document | 48 |
| Event | 8 |
| Claim | 24 |
| Evidence | 56 |
| 独立来源组 | 13 |
| 争议或待核实主张 | 9 |

## 已跑通的闭环

```text
网页/RSS 导入
→ URL 规范化与正文抽取
→ SHA-256 与重复文档识别
→ Source、Document、Snapshot 持久化
→ Event、Entity、Claim、Evidence 关联
→ 证据正文精确子串校验
→ 文档数与独立来源数分离
→ 结构化研判
→ 带证据索引的 HTML 简报
→ 回到带高亮的文档快照
```

## 页面验收

首页能够展示专题范围、事件态势图、事件演变时间线、事件流、结构化研判、信息缺口和来源状态。

筛选器支持关键词、重要性、来源类型、语言和争议主张。

事件详情能够展示时间、地点、重要性和置信度，相关实体与角色，文档数量与独立来源数量，每个独立来源组包含的文档和来源，支持、争议和待核实主张，证据片段、立场、强度、来源、发布时间、抓取时间和正文哈希，以及带证据高亮的文档快照。

报告能够从选定事件生成，证据使用 `E1`、`E2` 等索引，并保留信息缺口、关键假设、替代解释和证伪条件。

## API 验收

主要接口：

```text
GET  /pldr-api/health
GET  /pldr-api/v1/overview
GET  /pldr-api/v1/events
GET  /pldr-api/v1/events/{event_id}
GET  /pldr-api/v1/claims/{claim_id}/evidence
GET  /pldr-api/v1/sources/health
GET  /pldr-api/v1/timeline
GET  /pldr-api/v1/snapshot
GET  /pldr-api/v1/config
POST /pldr-api/v1/reports
POST /pldr-api/v1/import/url
POST /pldr-api/v1/import/rss
POST /pldr-api/v1/model/task
```

## 安全与边界

- 网页抓取只允许公共 HTTP/HTTPS 地址。
- localhost、私网、回环和保留地址会被阻止。
- 离线 HTML 与 RSS XML 导入不会访问外部网络。
- 快照正文和证据在输出时进行 HTML 转义。
- 报告文件名经过安全化处理。
- 模型未配置时采用确定性降级，不生成虚构证据。

## 自动验收

运行：

```bash
./scripts/test-p0.sh
```

验收覆盖最小数据规模、证据精确子串、来源独立性、分层判断、SSRF 防护、事件与证据 API、快照高亮、HTML 报告、离线网页/RSS 导入和无模型 Key 降级。

## 已知缺口

1. 当前页面是 World Monitor 兼容改造壳，完整上游源码尚未导入。
2. 演示文档是人工改写摘要，尚未形成真实网页取证库。
3. 网页和 RSS 导入后暂不自动并入 Event，需要 P0.1 增加候选事件聚类与审核工作流。
4. 尚未连续运行 7 天验证来源掉线、重试、页面变化和增量更新。
5. 尚未建立用户、权限、审批和完整审计日志。
6. 尚未决定长期 AGPL、商业许可或自有前端路线。
