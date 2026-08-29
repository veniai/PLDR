const {
  StatusPill,
  NavButton,
  PageHeading,
  Surface,
  StatCard,
  AttentionCard,
  HealthRow,
  Filters,
  SourceTable,
  RunList,
  VersionList,
  DiffViewer,
  ReviewQueue,
  DocumentPane,
  CandidatePane,
  CommandPalette,
  Toast,
} = window.PLDR_COMPONENTS;

const DATA = window.PLDR_MOCK_DATA;

const NAV_ITEMS = [
  { id: "today", label: "今日", icon: "01" },
  { id: "sources", label: "采集源", icon: "02", count: 5 },
  { id: "changes", label: "网页变化", icon: "03", count: 3 },
  { id: "review", label: "待审箱", icon: "04", count: 5 },
  { id: "events", label: "事件档案", icon: "05" },
];

const ROUTES = new Set(NAV_ITEMS.map((item) => item.id));

function TodayScreen({ navigate, openCapture, openStat, openAttention }) {
  return (
    <section className="screen" data-screen-label="今日注意力队列">
      <PageHeading
        eyebrow="P1 · RELIABLE COLLECTION"
        title="今天需要处理什么"
        description="所选 24 小时内的新增、变化、待审与故障。地图只呈现有明确坐标的相关事件，不替代任务队列。"
        actions={<><button className="ghost-button" onClick={() => navigate("sources")}>查看采集状态</button><button className="primary-button" onClick={openCapture}>＋ 添加输入</button></>}
      />

      <div className="stats-grid">
        {DATA.stats.map((stat) => <StatCard stat={stat} key={stat.id} onClick={() => openStat(stat)} />)}
      </div>

      <div className="today-layout">
        <Surface title="优先处理" subtitle="按影响与可信度排序 · 不是资讯流" actions={<StatusPill label="4 项需注意" tone="amber" />}>
          <div className="attention-list">
            {DATA.attention.map((item) => <AttentionCard item={item} key={item.id} onOpen={openAttention} />)}
          </div>
        </Surface>

        <aside className="right-rail">
          <Surface title="采集覆盖" subtitle="42 次计划检查 · 40 次完成">
            <div className="surface-body">
              <div className="health-list">
                <HealthRow label="固定来源" detail="28 / 30 正常" value={93} />
                <HealthRow label="关键词发现" detail="4 / 4 正常" value={100} />
                <HealthRow label="原页取证" detail="2 项降级" value={84} tone="amber" />
              </div>
              <div className="coverage-note"><strong>覆盖不完整：</strong>Marine Traffic Bulletin 返回 403；该缺口会持续显示，不用空白结果冒充成功。</div>
            </div>
          </Surface>

          <Surface title="模型与成本" subtitle="候选生成，不直接写正式档案">
            <div className="surface-body">
              <div className="model-meter">
                <div className="model-metric"><small>当前模型</small><strong>GLM-5.3 Flash</strong></div>
                <div className="model-metric"><small>本时段成本</small><strong>¥ 0.38</strong></div>
                <div className="model-metric"><small>生成成功</small><strong>8 / 9</strong></div>
                <div className="model-metric"><small>平均等待</small><strong>7.8 s</strong></div>
              </div>
            </div>
          </Surface>

          <Surface title="相关地点" subtitle="仅 1 项材料有可靠坐标">
            <div className="surface-body">
              <div className="mini-map" aria-label="苏伊士运河位置示意">
                <span className="map-pin"></span><span className="map-label">Suez Canal · 3 项变化</span>
              </div>
              <div className="map-caption"><span>坐标来自已确认事件</span><button className="ghost-button button-small" onClick={() => navigate("events")}>查看事件位置</button></div>
            </div>
          </Surface>
        </aside>
      </div>
    </section>
  );
}

function SourcesScreen({ filter, setFilter, selectedSourceId, setSelectedSourceId, notify, navigate, openCapture }) {
  const filters = [
    { value: "all", label: "全部", count: DATA.sources.length },
    { value: "healthy", label: "正常", count: 3 },
    { value: "changed", label: "有变化", count: 1 },
    { value: "failed", label: "失败 / 重试", count: 2 },
  ];
  const visibleSources = DATA.sources.filter((source) => {
    if (filter === "all") return true;
    if (filter === "failed") return ["failed", "retrying"].includes(source.health);
    if (filter === "changed") return source.lastResult === "changed";
    return source.health === filter;
  });
  const selected = visibleSources.find((source) => source.id === selectedSourceId) || visibleSources[0] || DATA.sources[0];
  const sourceAction = (title, detail) => notify(title, `${selected.name} · ${detail}`);
  const alternateRuns = {
    "src-port": [
      { id: "run-rss-118", time: "10:39:07", result: "no_change", duration: "0.7s", bytes: "28 KB", detail: "RSS 无新增，原页哈希未变化" },
      { id: "run-rss-117", time: "10:24:02", result: "success", duration: "0.9s", bytes: "31 KB", detail: "新文档与原页快照已保存" },
    ],
    "src-bts": [
      { id: "run-bts-088", time: "08:05:31", result: "no_change", duration: "1.4s", bytes: "112 KB", detail: "正文提取结果未变化" },
      { id: "run-bts-087", time: "昨天 02:05", result: "success", duration: "1.7s", bytes: "109 KB", detail: "V8 已保存" },
    ],
    "src-marine": [
      { id: "run-web-066", time: "10:30:12", result: "failed", duration: "4.2s", bytes: "0 KB", detail: "原站返回 HTTP 403，未创建空快照" },
      { id: "run-web-065", time: "09:30:08", result: "failed", duration: "4.0s", bytes: "0 KB", detail: "Browser 连接器仍被拒绝，保留故障" },
      { id: "run-web-064", time: "08:30:05", result: "failed", duration: "3.8s", bytes: "0 KB", detail: "首次失败，已进入退避重试" },
    ],
    "src-logistics": [
      { id: "run-api-221", time: "10:18:10", result: "retrying", duration: "2.0s", bytes: "2 KB", detail: "上游超时，按退避策略等待第二次重试" },
      { id: "run-api-220", time: "09:18:02", result: "no_change", duration: "0.8s", bytes: "18 KB", detail: "结构校验通过，数据未变化" },
    ],
  };
  const displayedRuns = alternateRuns[selected.id] || DATA.sourceRuns;

  return (
    <section className="screen" data-screen-label="采集源与运行">
      <PageHeading
        eyebrow="COLLECTION SOURCES"
        title="采集源与运行"
        description="看清每个来源抓取什么、何时再运行、上次是否成功，以及每一次快照版本。普通 HTTP/RSS/API 优先，复杂网页才使用浏览器连接器。"
        actions={<><button className="ghost-button" onClick={() => sourceAction("已开始健康检查", "仅执行试抓，不生成正式对象")}>检查全部</button><button className="primary-button" onClick={openCapture}>＋ 添加输入</button></>}
      />
      <div className="sources-layout">
        <Surface title="来源目录" subtitle={`${visibleSources.length} 个代表性结果 · 原型仅展示专题子集`} actions={<Filters items={filters} value={filter} onChange={setFilter} />}>
          <SourceTable sources={visibleSources} selectedId={selected.id} onSelect={setSelectedSourceId} />
        </Surface>

        <aside className="surface source-detail">
          <div className="detail-hero">
            <div className="button-row"><StatusPill status={selected.health} /><StatusPill status={selected.lastResult} /></div>
            <h2>{selected.name}</h2><p>{selected.host} · {selected.type} · {selected.language}</p>
          </div>
          <div className="detail-grid">
            <div className="detail-field"><small>采集方式</small><strong>{selected.method}</strong></div>
            <div className="detail-field"><small>计划周期</small><strong>{selected.cadence}</strong></div>
            <div className="detail-field"><small>成功率</small><strong>{selected.successRate}%</strong></div>
            <div className="detail-field"><small>连续失败</small><strong>{selected.failures} 次</strong></div>
            <div className="detail-field"><small>来源独立组</small><strong>{selected.independence}</strong></div>
            <div className="detail-field"><small>快照版本</small><strong>{selected.versions} 版</strong></div>
          </div>
          <div className="surface-header"><div className="surface-title">最近运行</div><button className="ghost-button button-small" onClick={() => selected.id === "src-sca" ? navigate("changes") : sourceAction("该来源的版本历史已定位", "当前原型仅展开 SCA 的完整 Diff 示例")}>版本 Diff</button></div>
          <RunList runs={displayedRuns} />
          <div className="surface-body">
            <div className="button-row">
              <button className="primary-button button-small" onClick={() => sourceAction("已加入立即检查队列", "完成后将保存新的不可变快照")}>立即检查</button>
              <button className="ghost-button button-small" onClick={() => sourceAction("已暂停来源", "保留历史快照，不再按计划运行")}>暂停</button>
              {selected.failures > 0 && <button className="danger-button button-small" onClick={() => sourceAction("已安排重试", "原故障记录保持可见")}>重试失败任务</button>}
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

function ChangesScreen({ navigate, notify }) {
  const [selectedVersion, setSelectedVersion] = React.useState("V4");
  const [diffMode, setDiffMode] = React.useState("inline");
  const [onlyChanges, setOnlyChanges] = React.useState(false);
  const comparisons = {
    V4: { title: "V3 → V4 正文变化", subtitle: "今天 09:12 对比 10:42", chunks: DATA.diff, scale: "新增 2 段 · 删除 1 段 · 约 48 个词", impact: "既有事件 EVT-2026-014 的恢复时间与优先级" },
    V3: { title: "V2 → V3 正文变化", subtitle: "昨天 18:04 对比今天 09:12", chunks: [{ type: "same", old: "Navigation remains under controlled coordination.", next: "Navigation remains under controlled coordination." }, { type: "added", old: "", next: "Tug assistance and channel inspections are continuing." }], scale: "新增 1 段 · 无删除", impact: "记录航道检查仍在继续，已在昨日审核" },
    V2: { title: "V1 → V2 正文变化", subtitle: "昨天 11:10 对比 18:04", chunks: [{ type: "removed", old: "Transit is temporarily suspended.", next: "" }, { type: "added", old: "", next: "Navigation remains under controlled coordination." }], scale: "新增 1 段 · 删除 1 段", impact: "状态从暂停调整为受控协调，已在昨日审核" },
    V1: { title: "V1 初始快照", subtitle: "昨天 11:10 · 没有更早版本", chunks: [{ type: "same", old: "Transit is temporarily suspended pending inspection.", next: "Transit is temporarily suspended pending inspection." }], scale: "初始保存 · 无可比较版本", impact: "创建来源基线，不自动生成变化主张" },
  };
  const comparison = comparisons[selectedVersion];
  return (
    <section className="screen" data-screen-label="网页版本变化">
      <PageHeading
        eyebrow="IMMUTABLE SNAPSHOTS"
        title="网页版本变化"
        description="先固定版本，再比较正文。旧快照不会因为来源更新而被覆盖；候选证据始终指向当时看到的原句。"
        actions={<button className="primary-button" onClick={() => { notify("已定位已有待审项", "V3 → V4 已在 10:43 生成 intake-sca-v4，不会重复创建"); navigate("review"); }}>打开已有待审项</button>}
      />
      <div className="change-layout">
        <Surface title="版本历史" subtitle="Suez Canal Authority">
          <VersionList versions={DATA.versions} selected={selectedVersion} onSelect={setSelectedVersion} />
        </Surface>

        <Surface title={comparison.title} subtitle={`${comparison.subtitle} · 正文提取器 v2`}>
          <div className="diff-toolbar">
            <div className="segmented">
              <button className={diffMode === "inline" ? "active" : ""} onClick={() => setDiffMode("inline")}>行内</button>
              <button className={diffMode === "split" ? "active" : ""} onClick={() => setDiffMode("split")}>左右</button>
            </div>
            <label className="check-control"><input type="checkbox" checked={onlyChanges} onChange={(e) => setOnlyChanges(e.target.checked)} />只看变化</label>
          </div>
          <DiffViewer chunks={comparison.chunks} mode={diffMode} onlyChanges={onlyChanges} oldLabel={selectedVersion === "V1" ? "无上一版本" : `V${Number(selectedVersion.slice(1)) - 1}`} newLabel={selectedVersion} />
        </Surface>

        <Surface title="影响判断" subtitle="规则提示 + AI 候选，仍需人工确认">
          <div className="surface-body">
            <div className="impact-list">
              <div className="impact-card"><small>变化规模</small><strong>{comparison.scale}</strong></div>
              <div className="impact-card"><small>可能影响</small><strong>{comparison.impact}</strong></div>
              <div className="impact-card"><small>来源性质</small><strong>官方机构 · 第一方材料 · 独立组 1</strong></div>
              <div className="impact-card"><small>下一步</small><strong>{selectedVersion === "V4" ? "打开已有待审项，逐句核对后决定是否合并" : "历史版本已处理；仅用于审计复核"}</strong></div>
            </div>
            <div className="evidence-check">✓ {selectedVersion === "V4" ? "两处候选证据都可在 V4 正文中精确定位；删除段落仅记录变化，不自动推导“否定”主张。" : `${selectedVersion} 已保存不可变哈希，历史审核结果不会因本次查看而改变。`}</div>
          </div>
        </Surface>
      </div>
    </section>
  );
}

function ReviewScreen({ selectedReviewId, setSelectedReviewId, notify, navigate, onConfirmed, outcomes, onOutcome }) {
  const reviewItems = DATA.reviewItems.map((item) => outcomes[item.id] ? { ...item, ...outcomes[item.id], hasCandidate: false } : item);
  const selectedItem = reviewItems.find((item) => item.id === selectedReviewId) || reviewItems[0];
  const pendingCount = reviewItems.filter((item) => !["rejected", "confirmed"].includes(item.status)).length;
  const detail = DATA.reviewDetails[selectedItem.id] || {};
  const candidate = detail.candidate || (selectedItem.id === "intake-sca-v4" ? DATA.candidate : null);
  const makeEdits = (sourceCandidate) => {
    if (!sourceCandidate) return {};
    return sourceCandidate.claims.reduce((values, claim, index) => ({ ...values, [`claim${index}`]: claim.text }), {
      title: sourceCandidate.event.title,
      summary: sourceCandidate.event.summary,
    });
  };
  const [documentView, setDocumentView] = React.useState("material");
  const [disposition, setDisposition] = React.useState("合并");
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const [rejectionReason, setRejectionReason] = React.useState("");
  const [mobileStep, setMobileStep] = React.useState(0);
  const [drafts, setDrafts] = React.useState({});
  const edits = drafts[selectedItem.id] || makeEdits(candidate);
  const setEdits = (updater) => setDrafts((allDrafts) => {
    const current = allDrafts[selectedItem.id] || makeEdits(candidate);
    const next = typeof updater === "function" ? updater(current) : updater;
    return { ...allDrafts, [selectedItem.id]: next };
  });

  React.useEffect(() => {
    setPreviewOpen(false);
    setRejectionReason("");
    setDocumentView("material");
    setDisposition(candidate?.event.match?.startsWith("EVT-") ? "合并" : "新建");
  }, [selectedReviewId]);

  React.useEffect(() => setPreviewOpen(false), [disposition]);
  React.useEffect(() => setPreviewOpen(false), [rejectionReason]);

  const confirm = () => {
    if (!previewOpen || !candidate) return;
    if (disposition === "驳回") {
      onOutcome(selectedItem.id, { status: "rejected", type: "已驳回", eventMatch: `原因：${rejectionReason}` });
      notify("候选已驳回", "正式事件、主张与报告均未改变；人工决定已保留用于审计");
      setPreviewOpen(false);
      return;
    }
    onOutcome(selectedItem.id, { status: "confirmed", type: "已入档", eventMatch: `${disposition}完成 · 人工决定已记录` });
    onConfirmed({ disposition, candidate, edits });
    notify("人工确认已记录", `${candidate.claims.length} 条主张将以“${disposition}”方式进入正式档案`);
    window.setTimeout(() => navigate("events"), 750);
  };

  const paneClass = (step, extra = "") => `review-pane ${extra} ${mobileStep === step ? "mobile-active" : ""}`;

  return (
    <section className="screen" data-screen-label="三栏人工审核台">
      <PageHeading
        eyebrow="HUMAN REVIEW BOUNDARY"
        title="待审箱"
        description="左边选材料，中间核对不可变原文和版本差异，右边编辑机器候选并预览正式区变化。AI 只能提议，人工确认后才入档。"
        actions={<StatusPill label={`${pendingCount} 项待处理`} tone="purple" />}
      />
      <nav className="mobile-review-steps" aria-label="移动端审核步骤">
        {["1 选择材料", "2 核对原文", "3 决定入档"].map((label, index) => <button key={label} className={mobileStep === index ? "active" : ""} onClick={() => setMobileStep(index)}>{label}</button>)}
      </nav>
      <div className="review-shell">
        <aside className={paneClass(0)}>
          <div className="pane-header"><span><strong>待审队列</strong><small>按优先级与时间排序</small></span><StatusPill label={String(pendingCount)} tone="purple" /></div>
          <ReviewQueue items={reviewItems} selectedId={selectedItem.id} onSelect={setSelectedReviewId} />
          <div className="mobile-next"><button className="primary-button button-small" onClick={() => setMobileStep(1)}>核对这份材料 →</button></div>
        </aside>

        <main className={paneClass(1)}>
          <div className="pane-header"><span><strong>原始材料与证据</strong><small>{selectedItem.id}</small></span>{selectedItem.hasSnapshot ? <StatusPill label="快照已固定" tone="cyan" /> : <StatusPill label="无快照" tone="red" />}</div>
          {selectedItem.hasSnapshot ? (
            <DocumentPane item={selectedItem} candidate={candidate} detail={detail} view={documentView} setView={setDocumentView} />
          ) : (
            <div className="empty-state"><strong>{selectedItem.title}</strong>抓取阶段没有得到快照，因此不会生成候选或改动正式区。<div className="button-row" style={{ justifyContent: "center", marginTop: 12 }}><button className="danger-button button-small" onClick={() => notify("已重新排队抓取", "仍需成功固定原页快照，之后才会运行候选生成")}>重试采集</button></div></div>
          )}
          <div className="mobile-next"><button className="primary-button button-small" disabled={!selectedItem.hasCandidate} onClick={() => setMobileStep(2)}>查看候选 →</button></div>
        </main>

        <aside className={paneClass(2, "candidate-pane")}>
          <div className="pane-header"><span><strong>候选与人工决定</strong><small>机器原值、人工修改、正式结果分离</small></span>{selectedItem.hasCandidate ? <StatusPill label="未确认" tone="purple" /> : <StatusPill status={selectedItem.status} />}</div>
          {selectedItem.hasCandidate && candidate ? (
            <CandidatePane
              candidate={candidate}
              disposition={disposition}
              setDisposition={setDisposition}
              edits={edits}
              setEdits={setEdits}
              onPreview={() => setPreviewOpen((open) => !open)}
              previewOpen={previewOpen}
              onConfirm={confirm}
              rejectionReason={rejectionReason}
              setRejectionReason={setRejectionReason}
            />
          ) : ["rejected", "confirmed"].includes(selectedItem.status) ? (
            <div className="empty-state"><strong>{selectedItem.status === "rejected" ? "候选已驳回" : "候选已进入正式档案"}</strong>{selectedItem.eventMatch}<br />原始快照、机器原值与人工决定仍可审计。</div>
          ) : selectedItem.failureStage === "generation" ? (
            <div className="empty-state"><strong>快照已保存，但候选生成失败</strong>原始 API 响应仍可在中栏核对；模型结构无效不会抹掉快照，也不会写入正式档案。<div className="button-row" style={{ justifyContent: "center", marginTop: 12 }}><button className="primary-button button-small" onClick={() => notify("已重新排队生成", "复用 V21 快照，不会重新请求上游 API")}>重新生成候选</button></div></div>
          ) : <div className="empty-state"><strong>抓取阶段尚无快照</strong>请先重试采集；只有固定原始材料后才能生成候选。</div>}
        </aside>
      </div>
    </section>
  );
}

function EventsScreen({ navigate, recentConfirmation }) {
  const [tab, setTab] = React.useState("claims");
  const isNewEvent = recentConfirmation?.disposition === "新建";
  const isModifiedEvent = recentConfirmation?.disposition === "修改";
  const appliedTitle = recentConfirmation?.edits.title || recentConfirmation?.candidate.event.title;
  const appliedSummary = recentConfirmation?.edits.summary || recentConfirmation?.candidate.event.summary;
  const event = recentConfirmation ? (isNewEvent ? {
    ...DATA.event,
    id: "EVT-2026-015",
    title: appliedTitle,
    status: "已确认 · 新建",
    summary: appliedSummary,
    time: `${recentConfirmation.candidate.event.eventTime} — 持续观察`,
    location: recentConfirmation.candidate.event.location,
    documents: 1,
    sources: 1,
    claims: recentConfirmation.candidate.claims.length,
    evidence: recentConfirmation.candidate.claims.length,
    confidence: "初步",
  } : {
    ...DATA.event,
    title: isModifiedEvent ? appliedTitle : DATA.event.title,
    summary: appliedSummary,
  }) : DATA.event;
  const defaultNewRows = [
    { text: "管理局预计首批北向船队将在 24 小时内恢复通行，但仍取决于最终安全检查。", source: "Suez Canal Authority", count: "1 个独立来源", tone: "amber", status: "待交叉验证" },
    { text: "医疗物资和易腐货物运输船将被优先安排。", source: "Suez Canal Authority", count: "1 个独立来源", tone: "amber", status: "待交叉验证" },
  ];
  const baselineRows = [
    { text: "拖带协助与航道检查仍在继续。", source: "SCA / Port Said", count: "2 个独立来源", tone: "green", status: "已印证" },
    { text: "不同材料对完全恢复时间仍存在表述差异。", source: "Reuters / SCA", count: "2 个独立来源", tone: "red", status: "存在争议" },
    { text: "技术检查完成之前，恢复窗口仍可能调整。", source: "SCA / Port Said", count: "2 个独立来源", tone: "green", status: "已印证" },
  ];
  const confirmedRows = recentConfirmation?.candidate.claims.map((claim, index) => ({
    text: recentConfirmation.edits[`claim${index}`] || claim.text,
    source: recentConfirmation.candidate.entities[0]?.name || "已确认来源",
    count: "1 个独立来源",
    tone: "amber",
    status: "待交叉验证",
  })) || defaultNewRows;
  const claimRows = isNewEvent ? confirmedRows : [...confirmedRows, ...baselineRows].slice(0, 5);
  const impactCopy = isNewEvent
    ? `✓ 已新建 ${event.id}，写入 ${confirmedRows.length} 条人工确认主张；原句与机器原值保持可追溯。`
    : isModifiedEvent
      ? `✓ 已采用人工编辑值更新事件摘要与 ${confirmedRows.length} 条主张；旧版本仍保留在历史中。`
      : `✓ 已合并 ${confirmedRows.length} 条人工确认主张；每条都保留原句、快照哈希、机器原值与人工决定。`;
  return (
    <section className="screen" data-screen-label="正式事件档案">
      <PageHeading
        eyebrow="CONFIRMED EVENT DOSSIER"
        title="事件档案"
        description="这里只呈现经过人工确认的对象；每条主张仍可沿“来源—文档—快照—证据”回到当时原文。"
        actions={<button className="ghost-button" onClick={() => navigate("review")}>返回待审箱</button>}
      />
      <div className="event-hero">
        <div className="event-id">{event.id}</div>
        <h1>{event.title}</h1><p>{event.summary}</p>
        <div className="event-meta"><StatusPill label={event.status} tone="green" /><span>{event.time}</span><span>{event.location}</span><span>综合信心：{event.confidence}</span></div>
      </div>
      <div className="event-tabs" role="tablist">
        {[{id:"overview",label:"概览"},{id:"claims",label:`主张与证据 ${event.claims}`},{id:"sources",label:`材料 ${event.documents}`},{id:"history",label:"变更历史"}].map((item) => <button role="tab" aria-selected={tab === item.id} className={`event-tab ${tab === item.id ? "active" : ""}`} key={item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}
      </div>

      <div className="events-layout" style={{ marginTop: 14 }}>
        <Surface title={tab === "claims" ? "已确认主张与证据" : "档案内容"} subtitle="来源可靠性与信息置信度分别呈现">
          {tab === "claims" ? (
            <div className="claims-table">
              {claimRows.map((claim, index) => (
                <div className="claim-row" key={index}>
                  <span className="claim-check">✓</span>
                  <div className="claim-text">{claim.text}</div>
                  <div className="claim-source"><strong>{claim.source}</strong>{claim.count}</div>
                  <StatusPill label={claim.status} tone={claim.tone} />
                </div>
              ))}
            </div>
          ) : tab === "overview" ? (
            <div className="surface-body"><div className="impact-list"><div className="impact-card"><small>时间</small><strong>{event.time}</strong></div><div className="impact-card"><small>地点</small><strong>{event.location}</strong></div><div className="impact-card"><small>证据规模</small><strong>{event.sources} 个来源 · {event.evidence} 条证据</strong></div></div></div>
          ) : tab === "sources" ? (
            isNewEvent ? <div className="surface-body"><div className="impact-card"><small>当前来源</small><strong>{recentConfirmation.candidate.entities[0]?.name} · 1 个不可变快照</strong></div></div> : <div className="surface-body"><div className="impact-list"><div className="impact-card"><small>官方机构</small><strong>Suez Canal Authority · 4 个不可变快照</strong></div><div className="impact-card"><small>港口管理方</small><strong>Port Said Authority RSS · 2 个文档</strong></div><div className="impact-card"><small>媒体来源</small><strong>Reuters · 仅用于独立印证与争议记录</strong></div></div></div>
          ) : (
            <div className="timeline"><div className="timeline-item"><small>刚刚</small><strong>{recentConfirmation ? `人工${recentConfirmation.disposition}并采用最终编辑值` : "人工确认 2 条新主张，保留 V4 证据回链"}</strong></div>{!isNewEvent && <><div className="timeline-item"><small>今天 10:43</small><strong>V4 内容变化进入待审箱</strong></div><div className="timeline-item"><small>昨天 18:22</small><strong>创建事件并确认首批 3 条主张</strong></div></>}</div>
          )}
        </Surface>
        <aside className="right-rail">
          {recentConfirmation ? (
            <Surface title="本次入档影响" subtitle={`来自刚才的人工“${recentConfirmation.disposition}”`}>
              <div className="surface-body">
                <div className="evidence-check">{impactCopy}</div>
                <div className="coverage-note">仍需独立来源交叉验证；系统不会因为第一方来源就自动标记为多源印证。</div>
              </div>
            </Surface>
          ) : (
            <Surface title="最近正式变化" subtitle="没有本次会话内的入档操作">
              <div className="surface-body"><div className="impact-card"><small>最近确认</small><strong>昨天 18:22 · 创建事件并确认首批主张</strong></div></div>
            </Surface>
          )}
          <Surface title="证据链完整性" subtitle={`${event.evidence} 条证据`}>
            <div className="surface-body"><div className="health-list"><HealthRow label="原句可定位" detail={`${event.evidence} / ${event.evidence}`} value={100} /><HealthRow label="快照哈希" detail={`${event.evidence} / ${event.evidence}`} value={100} /><HealthRow label="多源印证" detail={isNewEvent ? `0 / ${event.claims} 主张` : "3 / 5 主张"} value={isNewEvent ? 0 : 60} tone="amber" /></div></div>
          </Surface>
        </aside>
      </div>
    </section>
  );
}

function CaptureDialog({ open, onClose, onChoose }) {
  const [mode, setMode] = React.useState("inputs");
  const [keyword, setKeyword] = React.useState("Suez Canal northbound convoy resumption");
  const [searchRan, setSearchRan] = React.useState(false);
  React.useEffect(() => {
    if (open) {
      setMode("inputs");
      setSearchRan(false);
    }
  }, [open]);
  if (!open) return null;
  const inputs = [
    { id: "url", icon: "↗", title: "网页 / RSS", detail: "固定 URL 定时检查，保存每次版本" },
    { id: "api", icon: "{}", title: "确定 API", detail: "按字段映射接入结构化数据" },
    { id: "text", icon: "¶", title: "粘贴文本", detail: "保留提交原文并生成待审候选" },
    { id: "file", icon: "▤", title: "本地文件", detail: "PDF、HTML、Markdown 或纯文本" },
    { id: "search", icon: "⌕", title: "关键词发现", detail: "主动搜索线索；抓取原页后才可作为证据" },
  ];
  return (
    <div className="command-overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="capture-dialog" role="dialog" aria-modal="true" aria-label="添加输入">
        <header>
          <div>
            <p className="page-eyebrow">{mode === "search" ? "KEYWORD DISCOVERY" : "PARALLEL INPUTS"}</p>
            <h2>{mode === "search" ? "用关键词主动发现线索" : "从哪里添加材料？"}</h2>
            <p>{mode === "search" ? "搜索结果只是线索；必须成功抓取原页与固定快照，才能送入待审箱。" : "这些入口是并行输入；都先进入受控采集与待审边界，不会直接改正式档案。"}</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        {mode === "inputs" ? (
          <div className="capture-grid">
            {inputs.map((input) => <button className={`capture-option ${input.id === "search" ? "featured" : ""}`} key={input.id} onClick={() => input.id === "search" ? setMode("search") : onChoose(input)}><span className="capture-icon">{input.icon}</span><span><strong>{input.title}</strong><small>{input.detail}</small></span>{input.id === "search" && <span className="badge tone-cyan">可主动发现</span>}</button>)}
          </div>
        ) : (
          <div className="search-discovery">
            <div className="search-form-row">
              <button className="icon-button" onClick={() => setMode("inputs")} aria-label="返回输入选择">←</button>
              <input className="search-input" value={keyword} onChange={(event) => setKeyword(event.target.value)} aria-label="搜索关键词" />
              <button className="primary-button" onClick={() => setSearchRan(true)}>搜索</button>
            </div>
            <div className="search-scope-row"><StatusPill label="开放网页" tone="cyan" /><span>时间范围：过去 7 天</span><span>语言：自动</span><span>最多 20 条</span></div>
            {searchRan ? (
              <div className="discovery-results">
                <div className="discovery-summary"><strong>发现 3 条可抓取线索</strong><span>结果已去重；尚未成为 PLDR 证据</span></div>
                {[
                  { host: "suezcanal.gov.eg", title: "Statement on the phased resumption of northbound transit", meta: "18 分钟前 · 官方机构", match: "与 EVT-2026-014 高度相关" },
                  { host: "reuters.com", title: "Shipping operators await confirmed Suez transit window", meta: "51 分钟前 · 媒体来源", match: "可能提供独立印证" },
                  { host: "portsaid.gov.eg", title: "Northbound vessel waiting statistics updated", meta: "1 小时前 · 港口管理方", match: "可能补充影响规模" },
                ].map((result, index) => (
                  <article className="discovery-result" key={result.host}>
                    <div className="result-index">{String(index + 1).padStart(2, "0")}</div>
                    <div><small>{result.host}</small><strong>{result.title}</strong><p>{result.meta} · {result.match}</p></div>
                    <button className="ghost-button button-small" onClick={() => onChoose({ id: "search", title: "关键词发现" })}>抓取原页</button>
                  </article>
                ))}
              </div>
            ) : (
              <div className="search-empty"><span>⌕</span><strong>先搜索，再决定抓取哪条原页</strong><p>搜索接口不会直接生成事件、主张或证据，也不会把摘要当作原文。</p></div>
            )}
          </div>
        )}
        <footer>{mode === "search" ? "发现 → 选择结果 → 抓取原页 → 固定快照 → AI 生成候选 → 人工确认。" : "固定来源负责持续监测；关键词搜索用于补齐未知线索；两者最终都必须抓到可追溯原文。"}</footer>
      </section>
    </div>
  );
}

function App() {
  const hashRoute = window.location.hash.replace("#", "");
  const [activeRoute, setActiveRoute] = React.useState(ROUTES.has(hashRoute) ? hashRoute : "today");
  const [sourceFilter, setSourceFilter] = React.useState("all");
  const [selectedSourceId, setSelectedSourceId] = React.useState("src-sca");
  const [selectedReviewId, setSelectedReviewId] = React.useState("intake-sca-v4");
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [paletteQuery, setPaletteQuery] = React.useState("");
  const [captureOpen, setCaptureOpen] = React.useState(false);
  const [recentConfirmation, setRecentConfirmation] = React.useState(null);
  const [reviewOutcomes, setReviewOutcomes] = React.useState({});
  const [toast, setToast] = React.useState(null);
  const remainingReviewCount = DATA.reviewItems.filter((item) => !reviewOutcomes[item.id]).length;
  const visibleNavItems = NAV_ITEMS.map((item) => item.id === "review" ? { ...item, count: remainingReviewCount } : item);

  const navigate = React.useCallback((route) => {
    if (!ROUTES.has(route)) return;
    setActiveRoute(route);
    window.location.hash = route;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const notify = React.useCallback((title, detail) => {
    setToast({ title, detail });
    window.clearTimeout(window.__pldrToastTimer);
    window.__pldrToastTimer = window.setTimeout(() => setToast(null), 3200);
  }, []);

  React.useEffect(() => {
    const onHash = () => {
      const next = window.location.hash.replace("#", "");
      if (ROUTES.has(next)) setActiveRoute(next);
    };
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
      if (event.key === "Escape") {
        setPaletteOpen(false);
        setCaptureOpen(false);
      }
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("keydown", onKey);
    if (!window.location.hash) window.history.replaceState(null, "", "#today");
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const commands = [
    ...NAV_ITEMS.map((item) => ({ id: `route-${item.id}`, icon: item.icon, label: `打开${item.label}`, hint: "页面", route: item.id })),
    { id: "capture", icon: "+", label: "添加网页、文本、文件或搜索任务", hint: "输入", action: "capture" },
    { id: "event", icon: "E", label: "打开 EVT-2026-014 苏伊士航道恢复评估", hint: "事件", route: "events" },
  ];

  const chooseCommand = (item) => {
    setPaletteOpen(false);
    setPaletteQuery("");
    if (item.route) navigate(item.route);
    if (item.action === "capture") setCaptureOpen(true);
  };

  const chooseCapture = (input) => {
    setCaptureOpen(false);
    if (input.id === "search") {
      notify("已创建搜索发现采集项", "正在抓取所选原页；成功固定快照后才会进入待审箱");
    } else {
      notify(`已选择${input.title}`, "原型只演示入口；提交前会先显示试抓与证据预览");
    }
  };

  const openStat = (stat) => {
    if (stat.id === "new") {
      setSelectedReviewId("intake-port-28");
      navigate("review");
    } else if (stat.id === "failed") {
      setSourceFilter("failed");
      setSelectedSourceId("src-marine");
      navigate("sources");
    } else {
      navigate(stat.route);
    }
  };

  const openAttention = (item) => {
    if (item.id === "att-port") setSelectedReviewId("intake-port-28");
    if (item.id === "att-sca") setSelectedReviewId("intake-sca-v4");
    if (item.id === "att-failure") {
      setSourceFilter("failed");
      setSelectedSourceId("src-marine");
    }
    navigate(item.action || "review");
  };

  let screen;
  if (activeRoute === "sources") screen = <SourcesScreen filter={sourceFilter} setFilter={setSourceFilter} selectedSourceId={selectedSourceId} setSelectedSourceId={setSelectedSourceId} notify={notify} navigate={navigate} openCapture={() => setCaptureOpen(true)} />;
  else if (activeRoute === "changes") screen = <ChangesScreen navigate={navigate} notify={notify} />;
  else if (activeRoute === "review") screen = <ReviewScreen selectedReviewId={selectedReviewId} setSelectedReviewId={setSelectedReviewId} notify={notify} navigate={navigate} onConfirmed={setRecentConfirmation} outcomes={reviewOutcomes} onOutcome={(id, outcome) => setReviewOutcomes((old) => ({ ...old, [id]: outcome }))} />;
  else if (activeRoute === "events") screen = <EventsScreen navigate={navigate} recentConfirmation={recentConfirmation} />;
  else screen = <TodayScreen navigate={navigate} openCapture={() => setCaptureOpen(true)} openStat={openStat} openAttention={openAttention} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><span className="brand-mark">P</span><span><strong>PLDR</strong><small>证据优先研判台</small></span></div>
        <nav className="nav-section" aria-label="主导航">
          <div className="nav-label">Workbench</div>
          {visibleNavItems.map((item) => <NavButton item={item} key={item.id} active={activeRoute === item.id} onClick={() => navigate(item.id)} />)}
        </nav>
        <div className="sidebar-context">
          <div className="context-line"><span className="status-dot"></span><span>采集器正常 · 28/30</span></div>
          <div className="context-line"><span className="status-dot amber"></span><span>2 个来源覆盖缺失</span></div>
          <div className="context-line"><span className="status-dot"></span><span>模型候选隔离正常</span></div>
        </div>
        <div className="sidebar-version">P1 DESIGN PROTOTYPE<br />MOCK DATA · 2026-08-29</div>
      </aside>

      <header className="topbar">
        <div className="topic-switcher"><span><span className="topic-kicker">当前专题</span><span className="topic-name">海运关键通道与物流扰动</span></span></div>
        <span className="range-pill">过去 24 小时</span>
        <div className="topbar-spacer"></div>
        <button className="command-trigger" onClick={() => setPaletteOpen(true)}><span>搜索或跳转…</span><kbd>Ctrl K</kbd></button>
        <button className="ghost-button" onClick={() => navigate("sources")}><span className="status-dot"></span>系统状态</button>
        <button className="primary-button" onClick={() => setCaptureOpen(true)}>＋ 添加输入</button>
      </header>

      <main className="main">{screen}</main>

      <nav className="bottom-nav" aria-label="移动端主导航">
        {visibleNavItems.map((item) => <button className={activeRoute === item.id ? "active" : ""} key={item.id} onClick={() => navigate(item.id)}><span>{item.icon}</span><span>{item.label}</span></button>)}
      </nav>

      <CommandPalette open={paletteOpen} query={paletteQuery} setQuery={setPaletteQuery} items={commands} onChoose={chooseCommand} onClose={() => setPaletteOpen(false)} />
      <CaptureDialog open={captureOpen} onClose={() => setCaptureOpen(false)} onChoose={chooseCapture} />
      <Toast toast={toast} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
