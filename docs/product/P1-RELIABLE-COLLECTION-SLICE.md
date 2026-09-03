# P1 可靠采集纵向切片：固定网页与 RSS

更新时间：2026-08-31

本文档记录固定网页与 RSS 切片的具体契约和验收，不代表完整 P1。阶段级用户功能、性能指标、当前状态和退出条件以 `FUNCTIONAL-PERFORMANCE-REQUIREMENTS.md` 为准。

## 这一刀解决什么

本切片证明两条最短但真实的链路：

```text
固定公共网页 → 持久化运行队列 → 有界抓取与正文提取
            → 首次 / 未变化 / 已变化 / 失败
            → 不可变待审版本与确定性 Diff
            → AI 候选 → 人工新建、合并、修改候选后新建或驳回
            → 正式 Document 的新 Snapshot → Evidence 精确回链

RSS/Atom → 持久化运行队列 → 有界抓取与条目解析
        → GUID/URL + 内容指纹去重 → 新条目 / 重复 / 无效 / 失败
        → 来源提供的标题与摘要快照 → AI 候选 → 人工确认
```

它复用 P0.3 的候选与人工确认闭环，不再另做一套审核系统。**完成本切片不代表完整 P1 已退出**：首个真实专题包和统一“今天”输出、通用 API、专题必要的受控浏览器来源、30–50 个来源、连续 72 小时运行、安全与性能基准、PostgreSQL/PostGIS、模型调用成本和验收报告仍是后续 P1 工作。

## 对象和边界

- `CollectionTarget` 是“要定期检查哪个公共来源”的运行配置，不是正式 `Source`；`target_type` 当前为 `web_page` 或 `rss_feed`。
- 每次手动、到期或重试都会先落一条 `CollectionRun`，再由单 worker 领取；排队、运行、成功、失败和租约恢复均可追踪。
- 首次正文形成 V1；提取正文哈希未变化时只记 `unchanged`，不重复建版本、不重复调用候选生成；正文变化才形成 V2、V3……
- 确认前的完整原始/提取版本保存在 `IntakeItem`。Diff 只比较已经保存的相邻正文，不让 AI 猜变化。
- 抓取成功和候选生成成功是两件事：候选生成失败不会被伪装成抓取失败，条目可沿用 P0.3 的重新生成动作。
- 未确认、生成失败、撤销和驳回均不改变正式对象。
- 同一规范 URL 的新确认正文复用正式 `Document`，但新增不可变 `Snapshot`；`Document` 表示最新已确认头，旧 `Evidence` 永远固定旧快照，新 `Evidence` 固定新快照。
- RSS 条目用 `CollectionDiscoveredItem` 保存目标内稳定的条目指纹、首次/最近运行、状态和材料链接。同一 GUID/URL 加内容指纹重复出现只计入 duplicate，不重复生成待审项；内容变化会形成新的待审材料，但本切片不自动更新已确认对象。
- RSS 待审材料保存的是 feed 提供的标题和摘要合成快照，并把条目 URL 作为来源 URL；这不冒充已经抓取原文。后续是否逐条抓取原文，属于可靠采集的下一层增强。

## 分析员看到什么

工作台的“来源监测”入口提供：

1. 添加固定网页或 RSS/Atom、语言、检查周期和是否立即试抓。
2. 来源健康、上次成功、下次运行、逾期未采集、连续失败和最近错误。
3. 手动立即检查、暂停/恢复周期和失败重试。
4. 可分页追溯的运行历史和版本历史，以及 Vn 与 Vn-1 的正文 Diff；超大正文采用明确标注的有界视图，完整材料与哈希仍可打开核对。
5. 从版本直接打开待审箱；界面明确说明 AI 只是候选，“修改”指修改候选后新建，不会直接改写既有正式事件。
6. RSS 来源显示发现条目数、新条目/重复/无效计数、条目状态和最近出现时间；可从条目直接打开待审材料。

真实空状态、队列等待、抓取失败和候选失败都保留原样，不用演示数据补齐。

## 运行方式

### 本地

API 和 worker 共享同一个 `PLDR_DATABASE_URL`。先完成一次环境安装，然后分别在两个终端运行：

```bash
./scripts/setup-p0.sh
./scripts/run-p0.sh
```

```bash
./scripts/run-collector.sh
```

worker 也可以只处理至多一个任务后退出，便于计划任务或验收：

```bash
PYTHONPATH=services/intel-api .venv/bin/python -m pldr_api.collector --once
```

### Docker

```bash
docker compose up --build
```

Compose 会在 API 健康后启动 collector；两者共享持久化数据库目录。collector 默认启动 4 个受控工作槽，可通过 `PLDR_COLLECTOR_CONCURRENCY` 在 1–32 范围调整。

### 可配置项

```env
PLDR_MAX_FETCH_BYTES=5242880
PLDR_FETCH_TOTAL_TIMEOUT_SECONDS=30
PLDR_DIRECT_FETCH_TIMEOUT_SECONDS=12
PLDR_COLLECTION_POLL_SECONDS=2
PLDR_READER_FALLBACK_ENABLED=false
PLDR_READER_BASE_URL=https://r.jina.ai
PLDR_READER_PROXY_URL=
PLDR_READER_VALIDATION_DOH_URL=
```

`PLDR_MAX_FETCH_BYTES` 同时约束手动 URL 导入和固定网页采集的响应正文；`PLDR_FETCH_TOTAL_TIMEOUT_SECONDS` 是 DNS、直抓、重定向和 Reader 兜底共享的总墙钟上限。DNS 解析放在线程中等待，避免一个慢解析阻塞其他并发任务。直抓默认最多占用 12 秒，为 Reader 的浏览器渲染保留时间。每个直抓及重定向地址仍须解析到公网地址并固定连接，绝不因提高成功率而放行内网地址。

部署网络存在 DNS 污染或代理出口时，可同时配置 `PLDR_READER_PROXY_URL` 和可信 HTTPS `PLDR_READER_VALIDATION_DOH_URL`。这两个配置只接受不含用户名、密码、查询串和片段的 URL，密钥不得拼入地址。系统先用 DoH 校验目标解析结果全部为公网地址，再让远程 Reader 抓取；任一非公网结果、DoH 异常或 Reader 异常都失败关闭。代理和 DoH 均为空时沿用系统 DNS。

Jina Reader 会把公开目标 URL 发送给第三方服务，因此默认关闭，启用前应确认数据、使用条款以及 Reader 自身的 SSRF 防护。PLDR 能保证本机不连接未验证的地址，并会复核 Reader 返回的最终 URL；但无法把 DoH 结果绑定到第三方实际连接，也看不到第三方内部的中间跳转。需要登录、携带内部凭据或不能交给第三方处理的网页不允许走该路径。

## API 契约

- `GET /pldr-api/v1/collection/summary`
- `GET /pldr-api/v1/collection/targets`
- `POST /pldr-api/v1/collection/targets`
- `GET /pldr-api/v1/collection/targets/{target_id}`
- `GET /pldr-api/v1/collection/targets/{target_id}/runs`
- `GET /pldr-api/v1/collection/targets/{target_id}/versions`
- `GET /pldr-api/v1/collection/targets/{target_id}/items`
- `POST /pldr-api/v1/collection/targets/{target_id}/run`
- `POST /pldr-api/v1/collection/targets/{target_id}/pause`
- `POST /pldr-api/v1/collection/targets/{target_id}/resume`
- `POST /pldr-api/v1/collection/runs/{run_id}/retry`
- `GET /pldr-api/v1/collection/runs/{run_id}/diff`

“立即检查”和“立即试抓”先返回已持久化的 queued run；真正网络访问由 worker 完成。因此 API 进程退出不会丢掉尚未执行的请求。

暂停不会删除已经排队的运行；该运行在恢复来源后继续可执行。Summary 的 queued 数只统计当前可执行的队列，避免把暂停队列误报为正在推进。

## 可证伪验收

使用同一个受控 URL 依次返回 A、A、B、失败、B：

1. 第一次成功产生 V1 和一条待审项；正式对象计数不变。
2. 第二次为 `unchanged`，无新版本、无新待审项。
3. 第三次产生 V2；V1 原文与哈希不变，Diff 能精确显示 A/B 差异，仍未污染正式区。
4. 第四次只产生失败运行，保存错误分类；重试关系可追踪。
5. 第五次若 B 已是当前版本，则为 `unchanged`，不重复建版本。
6. worker 中断后，过期 running 租约能回到 queued；同一 run 恢复不会留下第二条版本待审项。
7. 驳回 V2 后正式区不变；确认 V1、再把 V2 合并到同一事件后，正式 `Document` 只有一个、`Snapshot` 有两个，新旧 `Evidence` 分别精确命中对应快照；重复确认不增加对象。
8. 桌面和 580px 以下屏幕均能走完“来源 → 运行 → Diff → 待审 → 正式快照”。

RSS 使用受控 feed 依次返回两个条目、同一 feed、混合私网条目和 malformed XML：

1. 首次运行创建两条 `rss_collection` 待审材料，正式对象计数不变。
2. 同一 feed 再次运行计为 2 个 duplicate，不新增 Intake 或条目状态。
3. 私网条目只计入 invalid，不生成材料；其余合法条目仍可进入审核。
4. malformed XML 使 Run 失败并记录 `rss_parse`，不吞掉已有材料。
5. worker 在条目材料提交后、状态链接前中断时，租约重放能按 target/run/item_key 或 URL+raw hash 认领同一条材料，不重复入箱。

项目原生验证仍为：

```bash
./scripts/test-p0.sh
```

### 2026-08-29 本地候选验收记录

- `./scripts/test-p0.sh`：38 项通过；`docker compose config --quiet` 与 `git diff --check` 通过（脚本本身已包含 Python 编译和前端语法检查）。
- 真实 API + 独立 collector + 临时 SQLite：`example.com` 完成 V1、待审、人工确认、正式快照和再次抓取 `unchanged`。
- 动态公共文本页 `httpbin.org/uuid` 连续抓取形成 V1/V2；桌面和 390px 窄屏均能查看 Diff、进入 V2 待审、预览确认并打开固定到 V2 的正式快照。
- 单文件采集测试在外部 `PLDR_DATABASE_URL` 存在时仍改用测试临时库，外部哨兵表保持未改动。

以上只证明当前工作区的本地候选；不代替 GitHub CI、30–50 个业务来源或 72 小时连续运行验收。

### 2026-08-30 RSS 候选本地验收记录

- `./scripts/test-p0.sh`：87 项通过；Shell 语法、`docker compose config --quiet` 与 `git diff --check` 通过（脚本本身已包含 Python 编译和前端语法检查）。
- 受控自动契约覆盖 RSS 发现/去重、私网条目 fail-closed、malformed feed、旧表增量迁移和租约重放认领。
- 临时 SQLite + 浏览器走查覆盖 1440×900 与 390×844：RSS 表单提交后显示持久 queued run；受管来源显示类型、条目数和运行计数；发现条目可打开 P0.3 固定快照审核；窄屏无横向溢出。
- 该记录使用受控数据和临时库，不冒充真实外网 feed、GitHub CI、30–50 个来源或 72 小时连续运行验收。

## 当前不做

- API、需要登录的来源、浏览器渲染和 Browser Steps，以及 RSS 条目原文的自动追抓。
- 多 worker、分布式锁、30–50 来源规模承诺和 72 小时通过声明。
- PostgreSQL/PostGIS、正式迁移工具和模型调用成本台账。
- 自动确认、自动改写正式对象、自动发布报告。
- changedetection.io 或 ArchiveBox Connector；它们仍是后续可选增强，不替代 PLDR 的证据链。

## 已知运行风险

- SQLite 首切片限定单 collector；API 与 worker 的写入并发仍不等价于 PostgreSQL 队列。
- 当前租约只用于单 collector 崩溃恢复，不提供多 worker fencing；部署第二个 collector 不在本切片支持范围内。
- 租约重放覆盖单次 worker 崩溃；若恢复过程中终态数据库提交再次失败，仍可能需要人工核对 Run 与 Intake trace，本切片不宣称连续双故障自动收敛。
- 地址在校验和实际连接之间仍存在 DNS 变化窗口，且同步 DNS 校验的极端卡顿可能晚于抓取墙钟上限返回；本切片只允许受信分析员配置来源，且继续拦截私网字面地址和每个重定向目标。
- 安全优先的有界抓取目前不接收 gzip/br 等压缩响应；强制压缩的来源会明确失败，后续只有在具备输出硬上限的流式解压器后才能放开。
- 当前模型候选仍可能把完整提取文本发送给已配置的外部模型；业务试点前必须补输入分块、调用日志、用量/成本和数据策略。
- Summary 当前按首切片规模读取运行历史做聚合；高频长期运行前需要改为数据库聚合/保留策略，不能据此宣称大规模查询能力。
- RSS 条目去重按目标隔离；同一文章从不同 feed 进入时仍会形成各自待审材料，正式去重继续交给人工确认和 Document 重复族处理。
- 部分 feed 的 GUID 不稳定或摘要为动态广告文案；当前内容指纹会如实产生新待审项，运营者需要在真实来源试点中评估来源质量。
- 若通过公网域名开放工作台，必须由反向代理或统一身份层保护写操作；CORS 不是认证。
