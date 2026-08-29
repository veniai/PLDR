# PLDR Intel API

P0/P1 独立情报后端，负责外部关键词检索适配、固定网页可靠采集、待处理采集箱、候选隔离、人工确认、来源、文档、快照、事件、实体、主张、证据、来源独立性、结构化研判、模型任务和 HTML 报告。

外部检索通过 Brave Search API 或自管 SearXNG 薄适配层调用；配置和证据边界见 `docs/product/P1-EXTERNAL-KEYWORD-DISCOVERY.md`。

固定网页采集由 API 负责管理来源与持久队列，由单独 worker 执行到期任务：

```bash
./scripts/run-p0.sh
# 另开一个终端
./scripts/run-collector.sh
```

首次正文和后续变化分别形成不可变待审版本；正文未变只记录一次成功运行。worker 不会把候选直接写入正式事件档案。完整边界与验收见 `docs/product/P1-RELIABLE-COLLECTION-SLICE.md`。
