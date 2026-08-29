const state = {
  overview: null,
  events: [],
  filteredEvents: [],
  selectedId: null,
  selectedEvent: null,
  sources: [],
  config: null,
  drawerTab: "overview",
  importMode: "url",
  intakeItems: [],
  intakeOptions: { events: [], entities: [] },
  selectedIntakeId: null,
  intakeDrafts: {},
  searchRun: null,
  searchResults: [],
  searchError: "",
  searchBusy: false,
  collectionSummary: null,
  collectionTargets: [],
  selectedCollectionTargetId: null,
  selectedCollectionTarget: null,
  collectionDiff: null,
  collectionBusy: false,
  collectionRequestSerial: 0,
  collectionDiffRequestSerial: 0,
  collectionPollTimer: null,
  loading: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const LABELS = {
  importance: { critical: "极高", high: "高", medium: "中", low: "低" },
  claim: {
    confirmed: "已确认",
    supported: "有支持",
    contested: "存在冲突",
    unverified: "待核实",
    refuted: "已反驳",
  },
  stance: { supports: "支持", contradicts: "冲突", context: "背景" },
  source: { healthy: "正常", stale: "陈旧", error: "异常", disabled: "停用" },
  mode: { "curated-demo": "人工整理演示", live: "实时专题", cached: "缓存专题" },
  intakeStatus: {
    parsed: "已解析",
    candidate_ready: "候选待审",
    generation_failed: "生成失败",
    confirmed: "已确认入档",
    rejected: "已驳回",
    cancelled: "已撤销",
    failed: "采集失败",
  },
  inputType: { web: "公共网页", text: "粘贴文本", file: "本地文件", rss: "RSS", search: "外部搜索结果", collection: "固定网页版本" },
  searchScope: { news: "新闻", web: "一般公开网页" },
  collectionStatus: {
    healthy: "正常",
    new: "待首次运行",
    degraded: "连续失败",
    error: "异常",
    paused: "已暂停",
    pending: "待首次运行",
    stale: "逾期未采集",
  },
  collectionRun: {
    queued: "等待运行",
    running: "正在抓取",
    succeeded: "抓取成功",
    failed: "抓取失败",
  },
  collectionOutcome: {
    baseline: "首次版本",
    changed: "正文变化",
    unchanged: "正文未变",
    failed: "抓取失败",
  },
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatDate(value, withTime = false) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function percent(value) {
  return `${Math.round(clamp(Number(value) || 0, 0, 1) * 100)}%`;
}

async function api(path, options = {}) {
  const isFormData = options.body instanceof FormData;
  const response = await fetch(path, {
    headers: isFormData ? {} : { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { detail: text || `HTTP ${response.status}` };
  }
  if (!response.ok) {
    const detail = payload?.detail?.message || payload?.detail || payload?.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

function setBusy(isBusy, label = "处理中") {
  state.loading = isBusy;
  document.body.classList.toggle("is-busy", isBusy);
  $("#system-state-text").textContent = isBusy ? label : "证据链已连接";
  $("#btn-refresh").disabled = isBusy;
}

function toast(message, type = "info", timeout = 4200) {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.innerHTML = `<span class="toast-icon">${type === "success" ? "✓" : type === "error" ? "!" : "i"}</span><span>${escapeHtml(message)}</span>`;
  $("#toast-stack").appendChild(node);
  requestAnimationFrame(() => node.classList.add("visible"));
  window.setTimeout(() => {
    node.classList.remove("visible");
    window.setTimeout(() => node.remove(), 220);
  }, timeout);
}

function eventSearchText(event) {
  return [
    event.title,
    event.summary,
    event.location?.name,
    ...(event.entities || []).flatMap((item) => [item.name, item.role, item.type]),
    ...(event.languages || []),
  ].join(" ").toLocaleLowerCase();
}

function applyFilters() {
  const query = $("#search").value.trim().toLocaleLowerCase();
  const importance = $("#importance-filter").value;
  const language = $("#language-filter").value;
  const contestedOnly = $("#contested-filter").checked;

  state.filteredEvents = state.events.filter((event) => {
    if (query && !eventSearchText(event).includes(query)) return false;
    if (importance && event.importance !== importance) return false;
    if (language && !(event.languages || []).includes(language)) return false;
    if (contestedOnly && !event.has_contested_claim) return false;
    return true;
  });

  renderEvents();
  renderMap();
  renderTimeline();
}

function renderTopic() {
  const topic = state.overview?.topic || {};
  $("#topic-title").textContent = topic.title || "未命名专题";
  $("#topic-description").textContent = topic.description || topic.subtitle || "";
  $("#topic-mode").textContent = LABELS.mode[topic.mode] || topic.mode || "专题模式";
  $("#topic-range").textContent = `${formatDate(topic.time_range?.start)} 至 ${formatDate(topic.time_range?.end)}`;
  $("#topic-updated").textContent = `更新 ${formatDate(state.overview?.last_updated, true)}`;
}

function renderMetrics() {
  const metrics = state.overview?.metrics || {};
  const intake = state.overview?.intake || {};
  const collection = state.collectionSummary?.metrics || state.collectionSummary || {};
  const changed = collection.changed_pending ?? collection.pending_changes ?? collection.pending_review ?? collection.changed ?? 0;
  const items = [
    ["events", metrics.events ?? 0, "事件"],
    ["documents", metrics.documents ?? 0, "文档"],
    ["independence", metrics.independence_groups ?? 0, "独立源组"],
    ["contested", metrics.contested_claims ?? 0, "争议主张"],
    ["intake", intake.candidate_ready ?? 0, "待审材料"],
    ["collection", changed, "监测待审"],
  ];
  $("#collection-alert-count").textContent = String(changed);
  $("#metrics").innerHTML = items.map(([key, value, label]) => `
    <div class="metric-card ${key}">
      <strong>${escapeHtml(value)}</strong>
      <span>${label}</span>
    </div>
  `).join("");
}

function renderEvents() {
  const root = $("#events");
  $("#event-count").textContent = String(state.filteredEvents.length);

  if (!state.filteredEvents.length) {
    root.innerHTML = `
      <div class="list-empty">
        <span>⌕</span>
        <p>当前筛选条件下没有事件。</p>
        <button type="button" class="text-btn" data-action="clear-filters">清除筛选</button>
      </div>`;
    return;
  }

  root.innerHTML = state.filteredEvents.map((event, index) => {
    const active = state.selectedId === event.id;
    const importance = LABELS.importance[event.importance] || event.importance;
    const claimCount = Object.values(event.claim_counts || {}).reduce((sum, value) => sum + value, 0);
    return `
      <article class="event-card ${active ? "active" : ""}" data-event-id="${escapeHtml(event.id)}" tabindex="0">
        <div class="event-card-index">${String(index + 1).padStart(2, "0")}</div>
        <div class="event-card-body">
          <div class="event-card-topline">
            <span class="importance-badge ${escapeHtml(event.importance)}">${escapeHtml(importance)}</span>
            <span>${formatDate(event.start_at)}</span>
            ${event.has_contested_claim ? '<span class="contested-flag">争议</span>' : ""}
          </div>
          <h3>${escapeHtml(event.title)}</h3>
          <p>${escapeHtml(event.summary)}</p>
          <div class="event-card-stats">
            <span>${event.document_count} 文档</span>
            <span>${event.independent_source_count} 独立源</span>
            <span>${claimCount} 主张</span>
          </div>
        </div>
      </article>`;
  }).join("");
}

function mapPosition(event) {
  const latitude = Number(event.location?.latitude);
  const longitude = Number(event.location?.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return {
    left: clamp(((longitude + 180) / 360) * 100, 3, 97),
    top: clamp(((90 - latitude) / 180) * 100, 5, 94),
  };
}

function renderMap() {
  const visibleIds = new Set(state.filteredEvents.map((event) => event.id));
  $("#markers").innerHTML = state.events.map((event) => {
    const position = mapPosition(event);
    if (!position) return "";
    const muted = !visibleIds.has(event.id);
    const active = state.selectedId === event.id;
    return `
      <button
        type="button"
        class="map-marker ${escapeHtml(event.importance)} ${active ? "active" : ""} ${muted ? "muted" : ""}"
        style="left:${position.left}%;top:${position.top}%"
        data-event-id="${escapeHtml(event.id)}"
        aria-label="${escapeHtml(event.title)}"
      >
        <span class="marker-core"></span>
        <span class="marker-ring"></span>
        <span class="marker-label">${escapeHtml(event.title.slice(0, 20))}</span>
      </button>`;
  }).join("");
}

function renderTimeline() {
  const root = $("#timeline");
  if (!state.filteredEvents.length) {
    root.innerHTML = '<div class="timeline-empty">时间线上暂无匹配事件</div>';
    return;
  }
  root.innerHTML = state.filteredEvents.map((event, index) => `
    <button
      type="button"
      class="timeline-item ${state.selectedId === event.id ? "active" : ""}"
      data-event-id="${escapeHtml(event.id)}"
    >
      <span class="timeline-node"></span>
      <span class="timeline-date">${formatDate(event.start_at)}</span>
      <strong>${escapeHtml(event.title)}</strong>
      <small>${escapeHtml(event.location?.name || "地点未标注")}</small>
      ${index < state.filteredEvents.length - 1 ? '<i class="timeline-connector"></i>' : ""}
    </button>
  `).join("");
}

function renderSources() {
  const counts = { healthy: 0, stale: 0, error: 0, disabled: 0 };
  state.sources.forEach((source) => {
    counts[source.status] = (counts[source.status] || 0) + 1;
  });
  $("#source-summary").textContent = `${counts.healthy} / ${state.sources.length}`;

  const ordered = [...state.sources].sort((a, b) => {
    const rank = { error: 0, stale: 1, healthy: 2, disabled: 3 };
    return (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.reliability_tier - b.reliability_tier;
  });

  $("#sources").innerHTML = `
    <div class="source-overview">
      <div><strong>${counts.healthy}</strong><span>正常</span></div>
      <div><strong>${counts.stale}</strong><span>陈旧</span></div>
      <div><strong>${counts.error}</strong><span>异常</span></div>
    </div>
    <div class="source-scroll">
      ${ordered.slice(0, 12).map((source) => `
        <div class="source-row">
          <span class="source-status ${escapeHtml(source.status)}"></span>
          <div>
            <strong>${escapeHtml(source.name)}</strong>
            <small>${escapeHtml(source.independence_group)} · T${source.reliability_tier} · ${source.document_count} 篇</small>
          </div>
          <span class="source-label ${escapeHtml(source.status)}">${LABELS.source[source.status] || escapeHtml(source.status)}</span>
        </div>
      `).join("")}
    </div>`;
}

function currentGaps() {
  const eventGaps = state.selectedEvent?.assessment?.information_gaps || [];
  const topicGaps = state.overview?.information_gaps || [];
  return [...new Set([...eventGaps, ...topicGaps])].slice(0, 10);
}

function renderGaps() {
  const gaps = currentGaps();
  $("#gap-count").textContent = String(gaps.length);
  $("#gaps").innerHTML = gaps.length
    ? gaps.map((gap, index) => `
        <div class="gap-row">
          <span>${String(index + 1).padStart(2, "0")}</span>
          <p>${escapeHtml(gap)}</p>
        </div>`).join("")
    : '<div class="panel-empty">当前没有登记的信息缺口。</div>';
}

function renderAssessment() {
  const root = $("#assessment");
  const assessment = state.selectedEvent?.assessment;
  if (!assessment) {
    $("#assessment-confidence").textContent = "未选择事件";
    root.className = "panel-body empty-state";
    root.innerHTML = '<div class="empty-icon">◎</div><p>选择一个事件，查看当前判断、关键假设、替代解释与证伪条件。</p>';
    return;
  }

  $("#assessment-confidence").textContent = `置信度 ${percent(assessment.confidence)}`;
  root.className = "panel-body assessment-content";
  root.innerHTML = `
    <div class="judgement-block">
      <span>当前概率最高判断</span>
      <p>${escapeHtml(assessment.judgement)}</p>
      <div class="confidence-bar"><i style="width:${percent(assessment.confidence)}"></i></div>
    </div>
    <div class="analysis-columns">
      <div>
        <h3>关键假设</h3>
        ${renderCompactList(assessment.assumptions, "暂无登记")}
      </div>
      <div>
        <h3>替代解释</h3>
        ${renderCompactList(assessment.alternatives, "暂无登记")}
      </div>
      <div>
        <h3>证伪条件</h3>
        ${renderCompactList(assessment.falsifiers, "暂无登记")}
      </div>
    </div>`;
}

function renderCompactList(items = [], emptyText) {
  if (!items.length) return `<p class="muted">${escapeHtml(emptyText)}</p>`;
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function claimClass(status) {
  return ["confirmed", "supported", "contested", "unverified", "refuted"].includes(status)
    ? status
    : "unverified";
}

function renderDrawerSummary(event) {
  $("#drawer-title").textContent = event.title;
  $("#drawer-summary").innerHTML = `
    <p>${escapeHtml(event.summary)}</p>
    <div class="dossier-facts">
      <span><b>${formatDate(event.start_at)}</b>开始时间</span>
      <span><b>${escapeHtml(event.location?.name || "未标注")}</b>地点</span>
      <span><b>${event.document_count}</b>文档</span>
      <span><b>${event.independent_source_count}</b>独立来源</span>
      <span><b>${percent(event.confidence)}</b>事件置信度</span>
    </div>`;
}

function renderDrawer() {
  const root = $("#drawer-content");
  const event = state.selectedEvent;
  if (!event) {
    root.innerHTML = '<div class="drawer-loading">正在加载事件档案…</div>';
    return;
  }
  renderDrawerSummary(event);
  $$(".drawer-tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === state.drawerTab));

  if (state.drawerTab === "claims") {
    root.innerHTML = renderClaimsTab(event);
  } else if (state.drawerTab === "documents") {
    root.innerHTML = renderDocumentsTab(event);
  } else {
    root.innerHTML = renderOverviewTab(event);
  }
}

function renderOverviewTab(event) {
  const entities = event.entities || [];
  const assessment = event.assessment;
  return `
    <section class="dossier-section">
      <div class="section-heading"><span>01</span><h3>相关实体</h3></div>
      <div class="entity-grid">
        ${entities.length ? entities.map((entity) => `
          <div class="entity-card">
            <span>${escapeHtml(entity.type)}</span>
            <strong>${escapeHtml(entity.name)}</strong>
            <small>${escapeHtml(entity.role)}</small>
          </div>`).join("") : '<p class="muted">暂无实体。</p>'}
      </div>
    </section>
    <section class="dossier-section">
      <div class="section-heading"><span>02</span><h3>研判框架</h3></div>
      ${assessment ? `
        <div class="overview-judgement">
          <p>${escapeHtml(assessment.judgement)}</p>
          <span>生成方式 ${escapeHtml(assessment.generated_by)} · ${formatDate(assessment.generated_at, true)}</span>
        </div>
        <div class="dossier-two-col">
          <div><h4>关键假设</h4>${renderCompactList(assessment.assumptions, "暂无登记")}</div>
          <div><h4>替代解释</h4>${renderCompactList(assessment.alternatives, "暂无登记")}</div>
        </div>` : '<p class="muted">暂无研判。</p>'}
    </section>
    <section class="dossier-section">
      <div class="section-heading"><span>03</span><h3>信息缺口与证伪条件</h3></div>
      <div class="dossier-two-col">
        <div><h4>信息缺口</h4>${renderCompactList(assessment?.information_gaps, "暂无登记")}</div>
        <div><h4>证伪条件</h4>${renderCompactList(assessment?.falsifiers, "暂无登记")}</div>
      </div>
    </section>`;
}

function renderClaimsTab(event) {
  const claims = event.claims || [];
  if (!claims.length) return '<div class="panel-empty">当前事件还没有登记主张。</div>';
  return claims.map((claim, index) => `
    <section class="claim-card ${claimClass(claim.status)}">
      <div class="claim-heading">
        <span class="claim-index">C${String(index + 1).padStart(2, "0")}</span>
        <div>
          <div class="claim-meta">
            <span class="claim-status">${LABELS.claim[claim.status] || escapeHtml(claim.status)}</span>
            <span>置信度 ${percent(claim.confidence)}</span>
            <span>${claim.origin === "human-confirmed" ? "人工确认" : escapeHtml(claim.origin)}</span>
          </div>
          <h3>${escapeHtml(claim.text)}</h3>
        </div>
      </div>
      <div class="evidence-stack">
        ${(claim.evidence || []).length ? claim.evidence.map((evidence, evidenceIndex) => `
          <article class="evidence-card ${escapeHtml(evidence.stance)}">
            <div class="evidence-topline">
              <span>E${String(evidenceIndex + 1).padStart(2, "0")}</span>
              <b>${LABELS.stance[evidence.stance] || escapeHtml(evidence.stance)}</b>
              <small>强度 ${percent(evidence.strength)}</small>
            </div>
            <blockquote>${escapeHtml(evidence.snippet)}</blockquote>
            <footer>
              <span>${escapeHtml(evidence.document.source.name)} · ${formatDate(evidence.document.published_at)}</span>
              <a href="${escapeHtml(withEventContext(evidence.snapshot_url || evidence.document.snapshot_url, event.id))}" target="_blank" rel="noopener">查看证据快照 ↗</a>
            </footer>
          </article>`).join("") : '<p class="muted">该主张尚未连接原文证据。</p>'}
      </div>
    </section>`).join("");
}

function withEventContext(url, eventId) {
  if (!url) return "#";
  if (url.includes("event_id=")) return url;
  return `${url}${url.includes("?") ? "&" : "?"}event_id=${encodeURIComponent(eventId)}`;
}

function renderDocumentsTab(event) {
  const documents = event.documents || [];
  if (!documents.length) return '<div class="panel-empty">当前事件没有关联资料。</div>';
  return `
    <div class="document-toolbar">
      <span>${documents.length} 篇文档，来自 ${event.independent_source_count} 个独立来源组</span>
    </div>
    <div class="document-list">
      ${documents.map((document, index) => {
        const duplicate = document.metadata?.duplicate_of_document_id;
        return `
          <article class="document-card">
            <span class="document-index">${String(index + 1).padStart(2, "0")}</span>
            <div>
              <div class="document-meta">
                <span>${escapeHtml(document.source.name)}</span>
                <span>T${document.source.reliability_tier}</span>
                <span>${escapeHtml(document.language)}</span>
                ${duplicate ? '<span class="duplicate-chip">转载折叠</span>' : ""}
              </div>
                <h3>${escapeHtml(document.title || "未知标题")}</h3>
              <p>${formatDate(document.published_at, true)} · 抓取 ${formatDate(document.fetched_at, true)}</p>
              <small>SHA-256 ${escapeHtml(document.content_hash.slice(0, 18))}… · ${escapeHtml(document.source.independence_group)}</small>
            </div>
            <div class="document-actions">
              <a href="${escapeHtml(withEventContext(document.snapshot_url, event.id))}" target="_blank" rel="noopener">证据快照</a>
              ${document.canonical_url ? `<a href="${escapeHtml(document.canonical_url)}" target="_blank" rel="noopener noreferrer">原始链接</a>` : ""}
            </div>
          </article>`;
      }).join("")}
    </div>`;
}

function openDrawer(tab = "overview") {
  state.drawerTab = tab;
  $("#drawer-backdrop").hidden = false;
  requestAnimationFrame(() => {
    $("#drawer-backdrop").classList.add("visible");
    $("#event-drawer").classList.add("open");
    $("#event-drawer").setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
  });
  renderDrawer();
}

function closeDrawer() {
  $("#drawer-backdrop").classList.remove("visible");
  $("#event-drawer").classList.remove("open");
  $("#event-drawer").setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
  window.setTimeout(() => { $("#drawer-backdrop").hidden = true; }, 220);
}

async function selectEvent(eventId, { open = false, tab = "overview", syncUrl = true } = {}) {
  if (!eventId) return;
  state.selectedId = eventId;
  renderEvents();
  renderMap();
  renderTimeline();
  $("#btn-report").disabled = false;

  if (open) {
    state.selectedEvent = null;
    openDrawer(tab);
  }

  try {
    const event = await api(`/pldr-api/v1/events/${encodeURIComponent(eventId)}`);
    if (state.selectedId !== eventId) return;
    state.selectedEvent = event;
    renderAssessment();
    renderGaps();
    if (open || $("#event-drawer").classList.contains("open")) renderDrawer();
    if (syncUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("event", eventId);
      history.replaceState(null, "", url);
    }
  } catch (error) {
    toast(`事件加载失败：${error.message}`, "error");
  }
}

async function generateReport(eventIds = null) {
  const ids = eventIds || (state.selectedId ? [state.selectedId] : []);
  if (!ids.length) {
    toast("请先选择一个事件。", "error");
    return;
  }
  const event = state.events.find((item) => item.id === ids[0]);
  setBusy(true, "正在生成简报");
  try {
    const result = await api("/pldr-api/v1/reports", {
      method: "POST",
      body: JSON.stringify({
        event_ids: ids,
        title: event ? `PLDR 证据简报：${event.title}` : null,
      }),
    });
    toast(`简报已生成，共 ${result.evidence_count} 条证据。`, "success");
    window.open(result.url, "_blank", "noopener");
  } catch (error) {
    toast(`简报生成失败：${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

function openImportModal() {
  const modal = $("#import-modal");
  $("#import-result").textContent = "";
  $("#import-form").reset();
  setImportMode("url");
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  $("#import-url").focus();
}

function closeImportModal() {
  const modal = $("#import-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
}

function setImportMode(mode) {
  state.importMode = mode;
  $$(".import-tab").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
  const isUrlMode = mode === "url" || mode === "rss";
  const isTextMode = mode === "text";
  const isFileMode = mode === "file";
  $("#import-url-label").textContent = mode === "rss" ? "RSS / Atom 地址" : "公开网页地址";
  $("#import-url").placeholder = mode === "rss" ? "https://example.org/feed.xml" : "https://example.org/article";
  $("#import-url").required = isUrlMode;
  $("#import-url").disabled = !isUrlMode;
  $("#import-url-field").hidden = !isUrlMode;
  $("#import-text").required = isTextMode;
  $("#import-text").disabled = !isTextMode;
  $("#import-text-field").hidden = !isTextMode;
  $("#import-file").required = isFileMode;
  $("#import-file").disabled = !isFileMode;
  $("#import-file-field").hidden = !isFileMode;
  $("#import-title-field").hidden = mode === "rss" || mode === "file";
  $("#import-published-field").hidden = mode !== "text";
  $("#import-source-label").textContent = mode === "url" || mode === "rss" ? "来源说明" : "来源说明（必填）";
  $("#import-source").required = isTextMode || isFileMode;
}

function openExternalSearchModal() {
  const modal = $("#search-modal");
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  renderSearchProvider();
  $("#search-keyword").focus();
}

function closeExternalSearchModal() {
  const modal = $("#search-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
}

function renderSearchProvider() {
  const search = state.config?.external_search || {};
  const summary = $("#search-provider-summary");
  if (!summary) return;
  summary.className = `search-provider-summary ${search.configured ? "ok" : "warning"}`;
  summary.innerHTML = `
    <strong>${escapeHtml(search.component || search.provider || "外部检索后端")}</strong>
    <span>${escapeHtml(search.version || "版本未知")} · ${escapeHtml(search.license || "许可证未知")}</span>
    <small>${escapeHtml(search.deployment_boundary || "部署边界未知")}</small>
    ${search.configured ? "" : `<em>${escapeHtml(search.error || "尚未配置；不会用演示数据伪装结果。")}</em>`}
  `;
}

function searchSelectionLabel(result) {
  const selection = result.selection;
  if (!selection) return "未加入";
  const status = LABELS.intakeStatus[selection.intake_status || selection.status] || selection.status;
  if (selection.intake_status === "failed") return `${status} · 可重试`;
  return status;
}

function renderSearchResults() {
  const root = $("#search-results");
  if (!root) return;
  root.innerHTML = state.searchResults.length ? state.searchResults.map((result) => `
    <article class="search-result ${result.selection ? "selected" : ""}" role="listitem">
      <label class="search-select">
        <input type="checkbox" value="${escapeHtml(result.id)}" ${result.selection ? "disabled" : ""}>
        <span>${escapeHtml(searchSelectionLabel(result))}</span>
      </label>
      <div class="search-result-body">
        <div class="search-result-meta">
          <span>#${result.rank || "-"}</span>
          <span>${escapeHtml(result.site || "未知站点")}</span>
          <span>${escapeHtml(result.channel || result.provider || "未知渠道")}</span>
          <span>${formatDate(result.published_at, true)}</span>
        </div>
        <h3>${escapeHtml(result.title || "无标题")}</h3>
        ${result.snippet ? `<p>${escapeHtml(result.snippet)}</p>` : '<p class="muted">检索后端未返回摘要。</p>'}
        <a href="${escapeHtml(result.original_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(result.original_url)}</a>
        <div class="search-result-footer">
          <small>查询运行：${escapeHtml(result.query_run_id || "未知")} · 结果：${escapeHtml(result.id || "未知")}</small>
          ${result.selection?.retryable ? `<button class="btn btn-ghost warning" type="button" data-search-retry="${escapeHtml(result.id)}">重试抓取</button>` : ""}
        </div>
        ${result.selection?.last_error ? `<p class="validation-error">${escapeHtml(result.selection.last_error)}</p>` : ""}
      </div>
    </article>
  `).join("") : '<div class="search-empty">没有匹配结果。PLDR 不会用演示数据填充空结果。</div>';
  $$("input[type='checkbox']", root).forEach((input) => input.addEventListener("change", updateSearchSelectionCount));
  updateSearchSelectionCount();
}

function updateSearchSelectionCount() {
  const selected = $$("#search-results input[type='checkbox']:checked");
  $("#search-select").disabled = state.searchBusy || selected.length === 0;
  $("#search-selection-count").textContent = selected.length
    ? `已选择 ${selected.length} / ${state.searchResults.length} 个结果`
    : `共 ${state.searchResults.length} 个结果，未选择`;
}

async function submitExternalSearch(event) {
  event.preventDefault();
  if (state.searchBusy) return;
  const keyword = $("#search-keyword").value.trim();
  if (keyword.length < 2) {
    $("#search-status").className = "search-status error";
    $("#search-status").textContent = "请输入至少 2 个字符的关键词。";
    return;
  }
  state.searchBusy = true;
  state.searchError = "";
  const submit = $("#search-submit");
  submit.disabled = true;
  submit.textContent = "检索中";
  $("#search-select").disabled = true;
  $("#search-status").className = "search-status";
  $("#search-status").textContent = "正在调用外部检索后端…";
  try {
    const payload = await api("/pldr-api/v1/search", {
      method: "POST",
      body: JSON.stringify({
        keyword,
        scope: $("#search-scope").value,
        language: $("#search-language").value,
        limit: 10,
      }),
    });
    state.searchRun = payload;
    state.searchResults = payload.results || [];
    $("#search-status").className = "search-status ok";
    $("#search-status").textContent = `检索完成：${state.searchResults.length} 条结果 · ${payload.channel} · ${payload.latency_ms || 0} ms`;
    renderSearchResults();
  } catch (error) {
    state.searchError = error.message;
    state.searchResults = [];
    $("#search-status").className = "search-status error";
    $("#search-status").textContent = `检索失败：${error.message}。未生成演示结果。`;
    renderSearchResults();
  } finally {
    state.searchBusy = false;
    submit.disabled = false;
    submit.textContent = "检索";
    updateSearchSelectionCount();
  }
}

async function submitSelectedSearchResults() {
  const selectedIds = $$("#search-results input[type='checkbox']:checked").map((input) => input.value);
  if (!selectedIds.length || state.searchBusy) return;
  state.searchBusy = true;
  const button = $("#search-select");
  button.disabled = true;
  button.textContent = "抓取选中项";
  $("#search-status").className = "search-status";
  $("#search-status").textContent = "只抓取勾选结果；搜索摘要不会进入证据链。";
  try {
    const payload = await api("/pldr-api/v1/search/select", {
      method: "POST",
      body: JSON.stringify({ result_ids: selectedIds }),
    });
    const updates = new Map((payload.results || []).map((entry) => [entry.result_id, entry.result]));
    state.searchResults = state.searchResults.map((result) => updates.get(result.id) || result);
    const failures = (payload.results || []).filter((entry) => entry.intake_status === "failed").length;
    $("#search-status").className = `search-status ${failures ? "warning" : "ok"}`;
    $("#search-status").textContent = failures
      ? `已处理 ${payload.results?.length || 0} 项：${failures} 项抓取失败，错误已保留且可重试。`
      : `已处理 ${payload.results?.length || 0} 项，采集箱保留完整查询到结果追踪。`;
    renderSearchResults();
    await refreshIntakeData();
    toast("选中结果已进入待处理采集箱。", "success");
  } catch (error) {
    $("#search-status").className = "search-status error";
    $("#search-status").textContent = `加入采集箱失败：${error.message}`;
  } finally {
    state.searchBusy = false;
    button.textContent = "选中项加入采集箱";
    updateSearchSelectionCount();
  }
}

async function retryExternalSearchResult(resultId) {
  if (state.searchBusy) return;
  state.searchBusy = true;
  $("#search-status").className = "search-status";
  $("#search-status").textContent = "正在重试抓取原始页面…";
  try {
    const payload = await api(`/pldr-api/v1/search/results/${encodeURIComponent(resultId)}/retry`, { method: "POST" });
    state.searchResults = state.searchResults.map((result) => (result.id === resultId ? payload.result : result));
    $("#search-status").className = `search-status ${payload.intake_status === "failed" ? "warning" : "ok"}`;
    $("#search-status").textContent = payload.intake_status === "failed"
      ? `重试仍失败：${payload.error || payload.result?.selection?.last_error || "未知错误"}`
      : "重试完成，条目仍需候选审核和人工确认。";
    renderSearchResults();
    await refreshIntakeData();
  } catch (error) {
    $("#search-status").className = "search-status error";
    $("#search-status").textContent = `重试失败：${error.message}`;
  } finally {
    state.searchBusy = false;
    updateSearchSelectionCount();
  }
}

async function submitImport(event) {
  event.preventDefault();
  const mode = state.importMode;
  const url = $("#import-url").value.trim();
  const sourceName = $("#import-source").value.trim();
  const language = $("#import-language").value;
  const title = $("#import-title").value.trim();
  const published = $("#import-published").value;
  const submit = $("#import-submit");
  submit.disabled = true;
  submit.textContent = "正在抓取";
  $("#import-result").className = "import-result";
  $("#import-result").textContent = "正在保存材料并生成可核验候选…";

  try {
    let result;
    if (mode === "file") {
      const file = $("#import-file").files[0];
      if (!file) throw new Error("请选择一个本地文件。");
      const body = new FormData();
      body.append("file", file);
      body.append("source_description", sourceName);
      body.append("language", language);
      result = await api("/pldr-api/v1/intake/files", { method: "POST", body });
    } else if (mode === "text") {
      const body = {
        text: $("#import-text").value,
        source_description: sourceName,
        title: title || null,
        published_at: published ? new Date(published).toISOString() : null,
        language,
      };
      result = await api("/pldr-api/v1/intake/text", { method: "POST", body: JSON.stringify(body) });
    } else if (mode === "rss") {
      const body = { url, source_name: sourceName || "Imported RSS", language };
      result = await api("/pldr-api/v1/import/rss", { method: "POST", body: JSON.stringify(body) });
    } else {
      const body = { url, source_name: sourceName || null, title: title || null, language };
      result = await api("/pldr-api/v1/import/url", { method: "POST", body: JSON.stringify(body) });
    }
    const items = result.intake_items || [result.intake_item].filter(Boolean);
    const count = items.length;
    $("#import-result").className = "import-result success";
    $("#import-result").textContent = `已形成 ${count} 个采集箱条目；候选和正式区保持隔离。`;
    toast(`材料已进入待处理采集箱：${count} 条`, "success");
    await refreshData({ keepSelection: true, quiet: true });
    if (items[0]) await openIntakeModal(items[0].id, true);
  } catch (error) {
    $("#import-result").className = "import-result error";
    $("#import-result").textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "提交到采集箱";
  }
}

function intakeStatusClass(status) {
  if (status === "candidate_ready") return "success";
  if (status === "confirmed") return "info";
  if (["failed", "generation_failed", "rejected"].includes(status)) return "warning";
  return "";
}

function intakeTitle(item) {
  return item.title || item.search?.search_title || item.source?.description || item.file?.name || `${LABELS.inputType[item.input_type] || item.input_type}材料`;
}

function candidateList(item, type) {
  return (item.candidates || []).filter((candidate) => candidate.object_type === type);
}

function renderIntakeList() {
  const root = $("#intake-list");
  if (!root) return;
  const activeCount = state.intakeItems.filter((item) => ["parsed", "candidate_ready", "generation_failed", "failed"].includes(item.status)).length;
  const badge = $("#intake-count");
  if (badge) badge.textContent = String(activeCount);
  $("#intake-summary").textContent = `${activeCount} 条待处理 / ${state.intakeItems.length} 条记录`;
  root.innerHTML = state.intakeItems.length ? state.intakeItems.map((item) => `
    <button class="intake-item ${item.id === state.selectedIntakeId ? "active" : ""}" type="button" role="listitem" data-intake-id="${escapeHtml(item.id)}">
      <span class="intake-type">${escapeHtml(LABELS.inputType[item.input_type] || item.input_type)}</span>
      <strong>${escapeHtml(intakeTitle(item))}</strong>
      <small>${escapeHtml(LABELS.intakeStatus[item.status] || item.status)} · ${formatDate(item.created_at, true)}</small>
      ${item.error ? `<em>${escapeHtml(item.error)}</em>` : ""}
    </button>
  `).join("") : '<p class="muted intake-empty">采集箱暂无条目。</p>';
}

function renderIntakeDetail(item = null) {
  const root = $("#intake-detail");
  if (!root) return;
  if (!item) {
    root.innerHTML = '<div class="panel-empty">请选择一个采集箱条目查看材料、候选和人工处置。</div>';
    return;
  }
  if (item.status === "candidate_ready") {
    root.innerHTML = renderIntakeReview(item);
    return;
  }
  const machineCandidates = (item.candidates || []).map((candidate) => `
    <article class="candidate-card readonly">
      <header><b>${escapeHtml(candidate.object_type)}</b><span>${escapeHtml(candidate.source_mode)}</span></header>
      <pre>${escapeHtml(JSON.stringify(candidate.machine, null, 2))}</pre>
      ${candidate.validation_error ? `<p class="validation-error">${escapeHtml(candidate.validation_error)}</p>` : ""}
    </article>
  `).join("");
  const final = item.final_object_ids || {};
  root.innerHTML = `
    <article class="intake-status-card ${intakeStatusClass(item.status)}">
      <div>
        <span>${escapeHtml(LABELS.intakeStatus[item.status] || item.status)}</span>
        <strong>${escapeHtml(intakeTitle(item))}</strong>
      </div>
      ${item.error || item.candidate_generation?.error ? `<p>${escapeHtml(item.error || item.candidate_generation.error)}</p>` : ""}
      ${item.status === "generation_failed" ? '<div class="trace-links"><button class="btn btn-ghost" type="button" data-intake-action="regenerate">重新生成候选</button></div>' : ""}
      ${item.status === "failed" && item.search?.result_id ? `<div class="trace-links"><button class="btn btn-ghost warning" type="button" data-intake-action="retry-search" data-search-result-id="${escapeHtml(item.search.result_id)}">重试抓取原始页</button></div>` : ""}
    </article>
    ${renderIntakeFacts(item)}
    ${renderIntakeSnapshots(item)}
    ${machineCandidates ? `<section class="candidate-stack"><h3>机器候选保留</h3>${machineCandidates}</section>` : ""}
    ${item.status === "confirmed" ? renderConfirmedRecord(item, final) : ""}
    ${item.rejection_reason ? `<p class="validation-error">驳回原因：${escapeHtml(item.rejection_reason)}</p>` : ""}
  `;
}

function renderIntakeFacts(item) {
  const collection = item.collection || item.review?.collection || null;
  const collectionBoundary = {
    confirmed: "已由人工确认并进入正式档案；证据固定到本版本快照。",
    rejected: "已由人工驳回，未进入正式档案。",
    cancelled: "已由人工撤销，未进入正式档案。",
    generation_failed: "材料已保存，但候选生成失败；尚未进入正式档案，可重新生成。",
    parsed: "材料已保存，尚未生成可审核候选；未进入正式档案。",
    failed: "本材料处理失败，未进入正式档案。",
  }[item.status] || "机器候选，尚未人工确认；修改表示修改候选后新建，不会直接改写既有正式事件。";
  return `
    <dl class="intake-facts">
      <div><dt>输入类型</dt><dd>${escapeHtml(LABELS.inputType[item.input_type] || item.input_type)}</dd></div>
      <div><dt>来源说明</dt><dd>${escapeHtml(item.source?.description || "未知来源")}</dd></div>
      <div><dt>原始地址</dt><dd>${escapeHtml(item.source?.canonical_url || item.source?.url || "未知地址")}</dd></div>
      <div><dt>标题</dt><dd>${escapeHtml(item.title || "未知标题")}</dd></div>
      <div><dt>发布时间</dt><dd>${formatDate(item.published_at, true)}</dd></div>
      <div><dt>材料指纹</dt><dd>${escapeHtml(item.material?.extracted_hash || "未生成")}</dd></div>
      ${item.file?.name ? `<div><dt>文件</dt><dd>${escapeHtml(item.file.name)} · ${escapeHtml(item.file.media_type)} · ${item.file.size_bytes || 0} bytes</dd></div>` : ""}
      ${collection ? `
        <div><dt>固定来源版本</dt><dd>${escapeHtml(collection.target_name || collection.target_id || "未知来源")} · V${escapeHtml(collection.version_number ?? "?")}</dd></div>
        <div><dt>采集运行</dt><dd>${escapeHtml(collection.run_id || "未知运行")} · ${escapeHtml(LABELS.collectionOutcome[collection.outcome] || collection.outcome || "正文变化")}</dd></div>
        <div><dt>版本边界</dt><dd>${escapeHtml(collectionBoundary)}</dd></div>
      ` : ""}
      ${item.search ? `
        <div><dt>发现关键词</dt><dd>${escapeHtml(item.search.keyword || "未知")}</dd></div>
        <div><dt>检索范围 / 渠道</dt><dd>${escapeHtml(LABELS.searchScope[item.search.scope] || item.search.scope || "未知")} · ${escapeHtml(item.search.channel || item.search.provider || "未知")}</dd></div>
        <div><dt>搜索结果回链</dt><dd><a href="${escapeHtml(item.search.original_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.search.original_url)}</a></dd></div>
        <div><dt>查询 / 结果</dt><dd>${escapeHtml(item.search.query_run_id || "未知")} → ${escapeHtml(item.search.result_id || "未知")} · 排名 ${item.search.rank ?? "未知"}</dd></div>
        <div><dt>搜索摘要</dt><dd>${escapeHtml(item.search.search_snippet || "未返回；不作为证据")}</dd></div>
        ${item.search_history?.length ? `<div><dt>历次查询与结果</dt><dd><ul class="search-trace-list">${item.search_history.map((trace) => `<li>${escapeHtml(trace.keyword || "未知关键词")} · ${escapeHtml(trace.channel || trace.provider || "未知渠道")} · ${formatDate(trace.selected_at, true)} · ${escapeHtml(trace.result_id || "未知结果")}</li>`).join("")}</ul></dd></div>` : ""}
      ` : ""}
    </dl>`;
}

function renderIntakeSnapshots(item) {
  const raw = item.material?.raw_snapshot || "";
  const extracted = item.material?.extracted_snapshot || "";
  return `
    <details class="snapshot-box" open>
      <summary>提取文本快照（SHA-256 ${escapeHtml(item.material?.extracted_hash || "未知")}）</summary>
      <pre>${escapeHtml(extracted)}</pre>
    </details>
    <details class="snapshot-box">
      <summary>原始输入快照（SHA-256 ${escapeHtml(item.material?.raw_hash || "未知")}${item.material?.raw_encoding ? ` · ${escapeHtml(item.material.raw_encoding)}` : ""}）</summary>
      <pre>${escapeHtml(raw)}</pre>
    </details>`;
}

function renderConfirmedRecord(item, final) {
  return `
    <section class="confirmed-trace">
      <h3>人工确认与回链</h3>
      <p>处置：${escapeHtml(item.disposition)} · 分析员：${escapeHtml(item.reviewed_by)} · 时间：${formatDate(item.reviewed_at, true)}</p>
      <div class="trace-links">
        <button class="text-btn" type="button" data-intake-action="open-event" data-event-target="${escapeHtml(final.event)}">打开正式事件</button>
        <a href="/snapshots/${escapeHtml(final.snapshot)}" target="_blank" rel="noopener">打开正式快照</a>
      </div>
      <pre>${escapeHtml(JSON.stringify(item.confirmation_result, null, 2))}</pre>
    </section>`;
}

function renderIntakeReview(item) {
  const event = candidateList(item, "event")[0]?.machine?.fields || {};
  const entities = candidateList(item, "entity");
  const claims = candidateList(item, "claim");
  const evidence = candidateList(item, "evidence");
  const eventOptions = state.intakeOptions.events || [];
  const entityOptions = state.intakeOptions.entities || [];
  return `
    ${renderIntakeFacts(item)}
    ${renderIntakeSnapshots(item)}
    <form class="review-form" data-review-form="${escapeHtml(item.id)}">
      <section class="review-section">
        <h3>人工处置</h3>
        <div class="review-grid">
          <label><span>处置方式</span>
            <select id="intake-disposition">
              <option value="create">新建正式事件</option>
              <option value="merge">合并到既有事件</option>
              <option value="modify">修改候选后新建</option>
            </select>
          </label>
          <label><span>合并目标事件</span>
            <select id="intake-merge-event"><option value="">请选择既有事件</option>${eventOptions.map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.title)}</option>`).join("")}</select>
          </label>
          <label><span>分析员</span><input id="intake-analyst" value="analyst" maxlength="160"></label>
        </div>
      </section>
      <section class="review-section">
        <h3>候选事件修改</h3>
        <div class="review-grid">
          <label><span>标题（未知必须由人工补实）</span><input id="intake-event-title" value="${escapeHtml(event.title || "")}" maxlength="500"></label>
          <label><span>事件时间（未知留空）</span><input id="intake-event-start" value="${escapeHtml(event.event_time || item.published_at || "")}" placeholder="YYYY-MM-DDTHH:MM:SSZ"></label>
          <label><span>地点（未知留空）</span><input id="intake-event-location" value="${escapeHtml(event.location_name || "")}" maxlength="200"></label>
          <label><span>重要性</span><select id="intake-event-importance"><option value="medium">中</option><option value="high">高</option><option value="critical">极高</option><option value="low">低</option></select></label>
        </div>
        <label><span>摘要</span><textarea id="intake-event-summary" rows="4">${escapeHtml(event.summary || "")}</textarea></label>
      </section>
      ${entities.length ? `<section class="review-section"><h3>候选实体</h3>${entities.map((candidate) => `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <div class="review-grid">
            <label><span>名称</span><input data-entity-field="name" value="${escapeHtml(candidate.machine?.fields?.name || "")}"></label>
            <label><span>类型</span><input data-entity-field="entity_type" value="${escapeHtml(candidate.machine?.fields?.entity_type || "organization")}"></label>
            <label><span>角色</span><input data-entity-field="role" value="${escapeHtml(candidate.machine?.fields?.role || "related")}"></label>
            <label><span>处置</span><select data-entity-field="action"><option value="create">新建</option><option value="merge">合并</option><option value="exclude">排除</option></select></label>
            <label><span>合并目标实体</span><select data-entity-field="merge_entity_id"><option value="">请选择实体</option>${entityOptions.map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.name)}</option>`).join("")}</select></label>
          </div>
        </div>`).join("")}</section>` : ""}
      <section class="review-section"><h3>候选主张</h3>${claims.map((candidate) => `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <label><span>主张文本</span><textarea data-claim-field="text" rows="3">${escapeHtml(candidate.machine?.fields?.text || "")}</textarea></label>
          <div class="review-grid">
            <label><span>状态</span><select data-claim-field="status"><option value="unverified">待核实</option><option value="supported">有支持</option><option value="contested">存在冲突</option></select></label>
            <label><span>处置</span><select data-claim-field="action"><option value="create">新建</option><option value="exclude">排除</option></select></label>
          </div>
        </div>`).join("") || '<p class="muted">机器未提出主张候选；未知保持未知。</p>'}</section>
      <section class="review-section"><h3>候选证据（必须精确命中原句）</h3>${evidence.map((candidate) => `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <label><span>原文片段</span><textarea data-evidence-field="snippet" rows="3">${escapeHtml(candidate.machine?.fields?.snippet || "")}</textarea></label>
          <div class="review-grid">
            <label><span>立场</span><select data-evidence-field="stance"><option value="context">背景</option><option value="supports">支持</option><option value="contradicts">冲突</option></select></label>
            <label><span>处置</span><select data-evidence-field="action"><option value="include">纳入</option><option value="exclude">排除</option></select></label>
          </div>
          ${candidate.validation_error ? `<p class="validation-error">${escapeHtml(candidate.validation_error)}（不可确认）</p>` : `<p class="validation-ok">可定位：${candidate.machine?.fields?.start_offset}-${candidate.machine?.fields?.end_offset}</p>`}
        </div>`).join("")}</section>
      <section class="review-section">
        <h3>驳回</h3>
        <label><span>驳回原因</span><textarea id="intake-reject-reason" rows="2" placeholder="填写原因后执行驳回"></textarea></label>
      </section>
      <div id="intake-preview" class="intake-preview" aria-live="polite"></div>
      <div class="review-actions">
        <button class="btn btn-ghost" type="button" data-intake-action="preview">预览入档</button>
        <button class="btn btn-ghost warning" type="button" data-intake-action="cancel">撤销处理</button>
        <button class="btn btn-danger" type="button" data-intake-action="reject">驳回</button>
        <button class="btn btn-primary" type="button" data-intake-action="confirm">确认入档</button>
      </div>
    </form>`;
}

async function refreshIntakeData(preferredItemId = state.selectedIntakeId) {
  const [list, options] = await Promise.all([
    api("/pldr-api/v1/intake?limit=200"),
    api("/pldr-api/v1/intake/options"),
  ]);
  state.intakeItems = list.items || [];
  state.intakeOptions = options || { events: [], entities: [] };
  let preferredError = null;
  if (preferredItemId && !state.intakeItems.some((item) => item.id === preferredItemId)) {
    try {
      const olderItem = await api(`/pldr-api/v1/intake/${encodeURIComponent(preferredItemId)}`);
      state.intakeItems = [olderItem, ...state.intakeItems];
    } catch (error) {
      preferredError = error;
    }
  }
  const target = (preferredItemId && state.intakeItems.some((item) => item.id === preferredItemId)
    ? preferredItemId
    : null)
    || state.intakeItems.find((item) => item.status === "candidate_ready")?.id
    || state.intakeItems[0]?.id
    || null;
  state.selectedIntakeId = target;
  renderIntakeList();
  renderIntakeDetail(state.intakeItems.find((item) => item.id === target) || null);
  return { found: !preferredItemId || target === preferredItemId, error: preferredError };
}

async function openIntakeModal(itemId = null, quiet = false) {
  const modal = $("#intake-modal");
  if (!quiet && typeof modal.showModal === "function") modal.showModal();
  else if (!quiet) modal.setAttribute("open", "");
  const preferred = itemId || state.selectedIntakeId;
  const result = await refreshIntakeData(preferred);
  if (itemId && !result.found) {
    toast(`指定版本无法打开：${result.error?.message || "材料不存在"}`, "error", 7000);
  }
}

function closeIntakeModal() {
  const modal = $("#intake-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
}

function selectedIntakeItem() {
  return state.intakeItems.find((item) => item.id === state.selectedIntakeId) || null;
}

function buildConfirmation(item) {
  const value = (selector) => ($(selector)?.value || "").trim();
  const entityGroups = new Map();
  if ($$("[data-entity-field]").length) {
    $$("[data-entity-field]").forEach((input) => {
      const root = input.closest("[data-candidate]");
      const key = root.dataset.candidate;
      entityGroups.set(key, { ...(entityGroups.get(key) || {}), [input.dataset.entityField]: input.value });
    });
  }
  const entities = [...entityGroups.entries()].map(([candidate_key, fields]) => ({
      candidate_key,
      action: fields.action || "create",
      name: fields.name || "",
      entity_type: fields.entity_type || "organization",
      aliases: [],
      role: fields.role || "related",
      merge_entity_id: fields.merge_entity_id || null,
  }));
  const claimGroups = new Map();
  $$("[data-claim-field]").forEach((input) => {
    const key = input.closest("[data-candidate]")?.dataset.candidate;
    claimGroups.set(key, { ...(claimGroups.get(key) || {}), [input.dataset.claimField]: input.value });
  });
  const evidenceGroups = new Map();
  $$("[data-evidence-field]").forEach((input) => {
    const key = input.closest("[data-candidate]")?.dataset.candidate;
    evidenceGroups.set(key, { ...(evidenceGroups.get(key) || {}), [input.dataset.evidenceField]: input.value });
  });
  return {
    disposition: value("#intake-disposition") || "create",
    analyst: value("#intake-analyst") || "analyst",
    merge_event_id: value("#intake-merge-event") || null,
    event: {
      title: value("#intake-event-title"),
      summary: value("#intake-event-summary"),
      event_type: "incident",
      start_at: value("#intake-event-start") || null,
      location_name: value("#intake-event-location") || "Unknown",
      importance: value("#intake-event-importance") || "medium",
    },
    entities,
    claims: [...claimGroups.entries()].map(([candidate_key, fields]) => ({
      candidate_key,
      action: fields.action || "create",
      text: fields.text || "",
      status: fields.status || "unverified",
      confidence: 0.5,
      temporal_scope: "",
      merge_claim_id: null,
    })),
    evidence: [...evidenceGroups.entries()].map(([candidate_key, fields]) => ({
      candidate_key,
      action: fields.action || "include",
      snippet: fields.snippet || "",
      stance: fields.stance || "context",
      strength: 0.7,
      note: "",
    })),
  };
}

async function handleIntakeAction(action, domEvent = null) {
  const item = selectedIntakeItem();
  if (!item) return;
  if (action === "open-event") {
    const eventId = domEvent?.target?.dataset?.eventTarget;
    closeIntakeModal();
    await selectEvent(eventId, { open: true });
    return;
  }
  if (action === "regenerate") {
    try {
      await api(`/pldr-api/v1/intake/${item.id}/regenerate`, { method: "POST" });
      toast("候选已重新生成。", "success");
      await refreshIntakeData(item.id);
      renderIntakeDetail(selectedIntakeItem());
    } catch (error) {
      toast(`候选重新生成失败：${error.message}`, "error", 7000);
    }
    return;
  }
  if (action === "retry-search") {
    const searchResultId = domEvent?.target?.dataset?.searchResultId;
    if (!searchResultId) {
      toast("搜索结果追踪缺失，无法重试。", "error", 7000);
      return;
    }
    try {
      const result = await api(`/pldr-api/v1/search/results/${encodeURIComponent(searchResultId)}/retry`, { method: "POST" });
      toast(result.intake_status === "failed" ? `重试仍失败：${result.error || "未知错误"}` : "原始页重试完成，等待候选审核。", result.intake_status === "failed" ? "error" : "success", 7000);
      await refreshIntakeData(item.id);
    } catch (error) {
      toast(`原始页重试失败：${error.message}`, "error", 7000);
    }
    return;
  }
  if (item.status !== "candidate_ready") return;
  try {
    if (action === "preview") {
      const preview = await api(`/pldr-api/v1/intake/${item.id}/preview`, {
        method: "POST",
        body: JSON.stringify(buildConfirmation(item)),
      });
      const root = $("#intake-preview");
      root.className = `intake-preview ${preview.confirmable ? "ok" : "error"}`;
      root.innerHTML = `
        <strong>${preview.confirmable ? "可以确认入档" : "当前不可确认"}</strong>
        ${preview.errors?.length ? `<ul>${preview.errors.map((error) => `<li>${escapeHtml(error)}</li>`).join("")}</ul>` : ""}
        <pre>${escapeHtml(JSON.stringify(preview.formal, null, 2))}</pre>`;
      return;
    }
    if (action === "confirm") {
      const result = await api(`/pldr-api/v1/intake/${item.id}/confirm`, {
        method: "POST",
        body: JSON.stringify(buildConfirmation(item)),
      });
      toast(`已原子入档：${result.result.formal_object_ids.event}`, "success");
      await refreshData({ keepSelection: false, quiet: true, preferredEventId: result.result.formal_object_ids.event });
      await refreshIntakeData(item.id);
      renderIntakeDetail(selectedIntakeItem());
      return;
    }
    if (action === "reject") {
      const reason = $("#intake-reject-reason")?.value.trim();
      if (!reason) throw new Error("请填写驳回原因。");
      await api(`/pldr-api/v1/intake/${item.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason }),
      });
      toast("候选已驳回，未写入正式区。", "success");
    } else if (action === "cancel") {
      await api(`/pldr-api/v1/intake/${item.id}/cancel`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason: "Analyst cancelled before confirmation" }),
      });
      toast("处理已撤销，未写入正式区。", "success");
    }
    await refreshData({ keepSelection: true, quiet: true });
    await refreshIntakeData(item.id);
  } catch (error) {
    toast(`采集箱操作失败：${error.message}`, "error", 7000);
  }
}

function collectionMetrics() {
  return state.collectionSummary?.metrics || state.collectionSummary || {};
}

function collectionIntervalMinutes(target) {
  if (target.interval_minutes != null) return Number(target.interval_minutes);
  if (target.interval_seconds != null) return Math.max(1, Math.round(Number(target.interval_seconds) / 60));
  return null;
}

function collectionRunError(run) {
  if (!run?.error) return run?.error_message || "";
  if (typeof run.error === "string") return run.error;
  return run.error.message || run.error.class || "";
}

function collectionTargetStatus(target) {
  if (target.enabled === false || target.status === "paused") return "paused";
  if (["error", "degraded"].includes(target.health || target.status)) return target.health || target.status;
  if (target.overdue === true) return "stale";
  return target.health || target.status || (target.last_success_at ? "healthy" : "pending");
}

function renderCollectionSummary() {
  const metrics = collectionMetrics();
  const targets = metrics.targets || {};
  const runs = metrics.runs || {};
  const cards = [
    [targets.total ?? metrics.total_targets ?? state.collectionTargets.length, "固定来源"],
    [targets.healthy ?? metrics.healthy ?? 0, "运行正常"],
    [metrics.changed_pending ?? metrics.pending_changes ?? metrics.pending_review ?? 0, "版本待审"],
    [(targets.degraded ?? 0) + (targets.error ?? metrics.error ?? 0) + (targets.stale ?? 0), "需要恢复"],
    [(runs.queued ?? metrics.queued ?? 0) + (runs.running ?? metrics.running ?? 0), "队列中"],
  ];
  $("#collection-summary").innerHTML = cards.map(([value, label]) => `
    <div><strong>${escapeHtml(value ?? 0)}</strong><span>${escapeHtml(label)}</span></div>
  `).join("");
}

function renderCollectionTargets() {
  const root = $("#collection-target-list");
  if (!state.collectionTargets.length) {
    root.innerHTML = `
      <div class="collection-empty">
        <strong>还没有固定来源</strong>
        <p>在上方添加一个无需登录的公共网页。PLDR 不会用演示运行记录填充这里。</p>
      </div>`;
    return;
  }
  root.innerHTML = state.collectionTargets.map((target) => {
    const status = collectionTargetStatus(target);
    const active = target.id === state.selectedCollectionTargetId;
    return `
      <div role="listitem">
        <button class="collection-target ${active ? "active" : ""}" type="button" data-collection-target="${escapeHtml(target.id)}">
          <span class="collection-health ${escapeHtml(status)}"></span>
          <span class="collection-target-copy">
            <strong>${escapeHtml(target.name || "未命名来源")}</strong>
            <small>${escapeHtml(target.url || target.canonical_url || "地址未知")}</small>
            <em>${escapeHtml(LABELS.collectionStatus[status] || status)} · ${escapeHtml(collectionIntervalMinutes(target) ?? "?")} 分钟</em>
          </span>
          <span class="collection-target-count">V${escapeHtml(target.version_count ?? 0)}</span>
        </button>
      </div>`;
  }).join("");
}

function collectionRunLabel(run) {
  const outcome = run.outcome ? LABELS.collectionOutcome[run.outcome] || run.outcome : "";
  const status = LABELS.collectionRun[run.status] || run.status || "未知状态";
  return outcome && outcome !== status ? `${status} · ${outcome}` : status;
}

function collectionRunDuration(run) {
  if (run.status === "queued") return "尚未开始";
  if (run.status === "running") return "进行中";
  return run.duration_ms == null ? "耗时未知" : `${run.duration_ms} ms`;
}

function renderCollectionDiff(diff = null) {
  if (!diff) return '<div class="collection-diff-empty">选择 V2 及以后的版本查看正文变化。</div>';
  const stats = diff.stats || {};
  const lines = diff.lines || diff.diff || diff.segments || (diff.unified_diff ? diff.unified_diff.split("\n") : []);
  const rendered = lines.map((line) => {
    const text = typeof line === "string" ? line : line.text ?? line.value ?? "";
    const rawType = typeof line === "string" ? (line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : line.startsWith("@@") ? "hunk" : "context") : line.type || line.kind || line.operation || "context";
    const type = { added: "add", insert: "add", "+": "add", removed: "remove", delete: "remove", "-": "remove" }[rawType] || rawType;
    return `<div class="collection-diff-line ${escapeHtml(type)}"><span>${type === "add" ? "+" : type === "remove" ? "−" : " "}</span><code>${escapeHtml(text)}</code></div>`;
  }).join("");
  const limited = diff.truncated && (
    diff.truncated.exact_word_diff === false
    || diff.truncated.segments === true
    || diff.truncated.unified_diff === true
  );
  const versionLinks = limited ? `
    <div class="collection-diff-warning">
      <strong>当前为有界差异视图</strong>
      <span>为避免超大网页拖垮采集服务，部分正文会合并或截断显示；哈希和已保存的完整相邻版本不受影响。</span>
      <span class="collection-diff-hashes">上一版 ${escapeHtml((diff.previous?.body_hash || "未知").slice(0, 12))}… · 当前版 ${escapeHtml((diff.current?.body_hash || "未知").slice(0, 12))}…</span>
      <span class="collection-diff-links">
        ${diff.previous?.intake_item_id ? `<button class="text-btn" type="button" data-collection-action="review" data-intake-id="${escapeHtml(diff.previous.intake_item_id)}">打开上一版完整材料</button>` : ""}
        ${diff.current?.intake_item_id ? `<button class="text-btn" type="button" data-collection-action="review" data-intake-id="${escapeHtml(diff.current.intake_item_id)}">打开当前版完整材料</button>` : ""}
      </span>
    </div>` : "";
  return `
    <div class="collection-diff-head">
      <div><span>对比</span><strong>V${escapeHtml(diff.from?.version_number ?? diff.from_version ?? Math.max(0, Number(diff.version_number || 1) - 1))} → V${escapeHtml(diff.to?.version_number ?? diff.to_version ?? diff.version_number ?? "?")}</strong></div>
      <div class="collection-diff-stats"><span class="add">+${escapeHtml(stats.added ?? stats.added_lines ?? stats.added_words ?? 0)}</span><span class="remove">−${escapeHtml(stats.removed ?? stats.removed_lines ?? stats.removed_words ?? 0)}</span></div>
    </div>
    ${versionLinks}
    <div class="collection-diff-body">${rendered || '<div class="collection-diff-empty">正文没有可显示的行级变化。</div>'}</div>`;
}

function renderCollectionDetail(detail = state.selectedCollectionTarget) {
  const root = $("#collection-detail");
  if (!detail) {
    root.innerHTML = '<div class="panel-empty">选择一个来源，查看运行记录、版本变化和恢复动作。</div>';
    return;
  }
  const target = detail.target || detail;
  const runs = detail.runs || target.runs || [];
  const versions = detail.versions || target.versions || [];
  const runTotal = Number(detail.run_count ?? target.run_count ?? runs.length);
  const versionTotal = Number(detail.version_count ?? target.version_count ?? versions.length);
  const status = collectionTargetStatus(target);
  const paused = target.enabled === false || status === "paused";
  root.innerHTML = `
    <div class="collection-detail-head">
      <div>
        <span class="collection-status-chip ${escapeHtml(status)}">${escapeHtml(LABELS.collectionStatus[status] || status)}</span>
        <h3>${escapeHtml(target.name || "未命名来源")}</h3>
        <a href="${escapeHtml(target.url || target.canonical_url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(target.url || target.canonical_url || "地址未知")}</a>
      </div>
      <div class="collection-detail-actions">
        <button class="btn btn-primary" type="button" data-collection-action="run" data-target-id="${escapeHtml(target.id)}" ${paused ? 'disabled title="请先恢复周期"' : ""}>立即检查</button>
        <button class="btn btn-ghost" type="button" data-collection-action="${paused ? "resume" : "pause"}" data-target-id="${escapeHtml(target.id)}">${paused ? "恢复周期" : "暂停周期"}</button>
      </div>
    </div>
    <dl class="collection-facts">
      <div><dt>检查周期</dt><dd>${escapeHtml(collectionIntervalMinutes(target) ?? "?")} 分钟</dd></div>
      <div><dt>上次成功</dt><dd>${formatDate(target.last_success_at, true)}</dd></div>
      <div><dt>下次运行</dt><dd>${target.enabled === false ? "已暂停" : formatDate(target.next_run_at, true)}</dd></div>
      <div><dt>连续失败</dt><dd>${escapeHtml(target.consecutive_failures ?? 0)}</dd></div>
      ${target.last_error ? `<div class="wide"><dt>最近错误</dt><dd>${escapeHtml(target.last_error)}</dd></div>` : ""}
    </dl>
    <div class="collection-detail-grid">
      <section>
        <div class="collection-section-heading"><div><span class="panel-kicker">RUN HISTORY</span><h3>运行记录</h3></div><span>已载入 ${runs.length} / 共 ${runTotal} 次</span></div>
        <div class="collection-run-list">
          ${runs.length ? runs.map((run) => `
            <article class="collection-run ${escapeHtml(run.status || "unknown")}">
              <span class="collection-run-dot"></span>
              <div>
                <strong>${escapeHtml(collectionRunLabel(run))}</strong>
                <small>${formatDate(run.started_at || run.created_at || run.queued_at, true)} · ${escapeHtml(run.trigger || "manual")} · ${escapeHtml(collectionRunDuration(run))}</small>
                ${collectionRunError(run) ? `<p>${escapeHtml(collectionRunError(run))}</p>` : ""}
              </div>
              ${run.status === "failed" ? `<button class="text-btn warning" type="button" data-collection-action="retry" data-run-id="${escapeHtml(run.id)}" ${paused ? 'disabled title="请先恢复周期"' : ""}>重试</button>` : ""}
            </article>`).join("") : '<div class="collection-empty"><p>尚无运行记录。</p></div>'}
          ${runs.length < runTotal ? `<button class="text-btn collection-load-more" type="button" data-collection-action="more-runs" data-target-id="${escapeHtml(target.id)}">加载更早运行</button>` : ""}
        </div>
      </section>
      <section>
        <div class="collection-section-heading"><div><span class="panel-kicker">IMMUTABLE VERSIONS</span><h3>正文版本</h3></div><span>已载入 ${versions.length} / 共 ${versionTotal} 个</span></div>
        <div class="collection-version-list">
          ${versions.length ? versions.map((version) => {
            const runId = version.run_id || version.id;
            const intakeId = version.intake_item_id || version.current_intake_item_id || version.intake_chain?.current || version.intake?.id;
            const intakeStatus = version.intake_status || version.intake?.status;
            return `
              <article class="collection-version">
                <button type="button" data-collection-action="diff" data-run-id="${escapeHtml(runId)}" ${Number(version.version_number || 0) < 2 ? "disabled" : ""}>
                  <strong>V${escapeHtml(version.version_number ?? "?")}</strong>
                  <span>${escapeHtml(version.outcome === "baseline" ? "首次版本" : "查看与上一版差异")}</span>
                  <small>${formatDate(version.captured_at || version.completed_at || version.finished_at || version.created_at, true)}</small>
                </button>
                ${intakeId ? `<button class="text-btn" type="button" data-collection-action="review" data-intake-id="${escapeHtml(intakeId)}">${escapeHtml(LABELS.intakeStatus[intakeStatus] || intakeStatus || "打开版本材料")}</button>` : ""}
              </article>`;
          }).join("") : '<div class="collection-empty"><p>成功抓取后才会出现第一个正文版本。</p></div>'}
          ${versions.length < versionTotal ? `<button class="text-btn collection-load-more" type="button" data-collection-action="more-versions" data-target-id="${escapeHtml(target.id)}">加载更早版本</button>` : ""}
        </div>
        <div id="collection-diff" class="collection-diff">${renderCollectionDiff(state.collectionDiff)}</div>
      </section>
    </div>`;
}

async function loadCollectionTarget(targetId, { preserveDiff = false } = {}) {
  const requestSerial = ++state.collectionRequestSerial;
  if (!targetId) {
    state.collectionDiffRequestSerial += 1;
    state.collectionDiff = null;
    state.selectedCollectionTarget = null;
    renderCollectionDetail(null);
    return;
  }
  state.selectedCollectionTargetId = targetId;
  if (!preserveDiff) {
    state.collectionDiffRequestSerial += 1;
    state.collectionDiff = null;
  }
  renderCollectionTargets();
  $("#collection-detail").innerHTML = '<div class="panel-empty">正在加载来源运行与版本…</div>';
  try {
    const detail = await api(`/pldr-api/v1/collection/targets/${encodeURIComponent(targetId)}`);
    if (requestSerial !== state.collectionRequestSerial || targetId !== state.selectedCollectionTargetId) return;
    state.selectedCollectionTarget = detail;
    renderCollectionDetail();
  } catch (error) {
    if (requestSerial !== state.collectionRequestSerial) return;
    state.selectedCollectionTarget = null;
    $("#collection-detail").innerHTML = `<div class="collection-error"><strong>来源详情加载失败</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function scheduleCollectionPoll(delayOverrideMs = null) {
  if (state.collectionPollTimer) window.clearTimeout(state.collectionPollTimer);
  state.collectionPollTimer = null;
  const modal = $("#collection-modal");
  const detail = state.selectedCollectionTarget;
  const runs = detail?.runs || detail?.target?.runs || [];
  const hasPending = (detail?.enabled !== false && runs.some((run) => run.status === "queued" || run.status === "running"))
    || Number(collectionMetrics().runs?.queued || 0) > 0
    || Number(collectionMetrics().runs?.running || 0) > 0;
  if (!modal?.open) return;
  state.collectionPollTimer = window.setTimeout(async () => {
    await refreshCollectionData();
  }, Number.isFinite(delayOverrideMs) ? delayOverrideMs : (hasPending ? 2500 : 15000));
}

async function refreshCollectionData(preferredTargetId = null) {
  if (state.collectionBusy) {
    scheduleCollectionPoll();
    return;
  }
  state.collectionBusy = true;
  let collectionRetryDelayMs = null;
  const previousTargetId = state.selectedCollectionTargetId;
  try {
    const [summary, targets] = await Promise.all([
      api("/pldr-api/v1/collection/summary"),
      api("/pldr-api/v1/collection/targets"),
    ]);
    state.collectionSummary = summary;
    state.collectionTargets = targets.items || targets.targets || [];
    renderCollectionSummary();
    const targetId = preferredTargetId
      || (state.collectionTargets.some((target) => target.id === state.selectedCollectionTargetId) ? state.selectedCollectionTargetId : null)
      || state.collectionTargets[0]?.id
      || null;
    state.selectedCollectionTargetId = targetId;
    renderCollectionTargets();
    await loadCollectionTarget(targetId, { preserveDiff: targetId === previousTargetId });
    renderMetrics();
  } catch (error) {
    collectionRetryDelayMs = 15000;
    state.selectedCollectionTarget = null;
    $("#collection-summary").innerHTML = `<div class="collection-error"><strong>来源监测不可用</strong><p>${escapeHtml(error.message)}</p></div>`;
    $("#collection-target-list").innerHTML = '<div class="collection-empty"><p>没有伪造运行记录；请检查后端服务。</p></div>';
    $("#collection-detail").innerHTML = '<div class="collection-error"><strong>无法读取运行与版本</strong><p>请恢复后端连接后重试。</p></div>';
  } finally {
    state.collectionBusy = false;
    scheduleCollectionPoll(collectionRetryDelayMs);
  }
}

async function openCollectionModal() {
  const modal = $("#collection-modal");
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  await refreshCollectionData();
}

function closeCollectionModal() {
  const modal = $("#collection-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
  if (state.collectionPollTimer) window.clearTimeout(state.collectionPollTimer);
  state.collectionPollTimer = null;
}

async function submitCollectionTarget(event) {
  event.preventDefault();
  if (state.collectionBusy) return;
  state.collectionBusy = true;
  const button = $("#collection-add");
  button.disabled = true;
  button.textContent = "正在保存并加入队列…";
  try {
    const result = await api("/pldr-api/v1/collection/targets", {
      method: "POST",
      body: JSON.stringify({
        name: $("#collection-name").value.trim(),
        url: $("#collection-url").value.trim(),
        interval_seconds: Number($("#collection-interval").value) * 60,
        language: $("#collection-language").value,
        run_immediately: $("#collection-run-immediately").checked,
      }),
    });
    const run = result.run || result.queued_run || null;
    const runFailed = run?.status === "failed";
    toast(runFailed ? `来源已保存，但首次抓取失败：${collectionRunError(run) || "未知错误"}` : run?.status === "queued" ? "固定来源已保存，首次试抓已进入持久队列。" : "固定来源已保存。变化只会进入待审箱。", runFailed ? "error" : "success", 7000);
    $("#collection-source-form").reset();
    $("#collection-run-immediately").checked = true;
    state.collectionBusy = false;
    await refreshCollectionData(result.target?.id);
    try {
      await refreshData({ keepSelection: true, quiet: true });
    } catch (error) {
      toast(`来源已保存，但专题指标刷新失败：${error.message}`, "warning", 7000);
    }
  } catch (error) {
    toast(`添加来源失败：${error.message}`, "error", 7000);
  } finally {
    state.collectionBusy = false;
    button.disabled = false;
    button.textContent = "添加来源";
  }
}

async function handleCollectionAction(action, node) {
  if (state.collectionBusy) return;
  if (action === "review") {
    const intakeId = node.dataset.intakeId;
    closeCollectionModal();
    await openIntakeModal(intakeId);
    return;
  }
  if (action === "diff") {
    const targetId = state.selectedCollectionTargetId;
    const runId = node.dataset.runId;
    const requestSerial = ++state.collectionDiffRequestSerial;
    try {
      const diff = await api(`/pldr-api/v1/collection/runs/${encodeURIComponent(runId)}/diff`);
      if (
        requestSerial !== state.collectionDiffRequestSerial
        || targetId !== state.selectedCollectionTargetId
        || diff.target_id !== targetId
        || diff.run_id !== runId
      ) return;
      state.collectionDiff = diff;
      const diffRoot = $("#collection-diff");
      if (diffRoot) diffRoot.innerHTML = renderCollectionDiff(state.collectionDiff);
    } catch (error) {
      toast(`版本对比失败：${error.message}`, "error", 7000);
    }
    return;
  }
  if (action === "more-runs" || action === "more-versions") {
    const targetId = node.dataset.targetId || state.selectedCollectionTargetId;
    const detail = state.selectedCollectionTarget;
    if (!targetId || !detail || targetId !== state.selectedCollectionTargetId) return;
    const key = action === "more-runs" ? "runs" : "versions";
    const currentItems = detail[key] || [];
    const knownTotal = Number(detail[action === "more-runs" ? "run_count" : "version_count"] || currentItems.length);
    state.collectionBusy = true;
    node.disabled = true;
    try {
      const page = await api(`/pldr-api/v1/collection/targets/${encodeURIComponent(targetId)}/${key}?offset=${currentItems.length}&limit=100`);
      if (targetId !== state.selectedCollectionTargetId) return;
      if (Number(page.count) !== knownTotal) {
        toast("采集历史刚刚发生变化，已刷新后请再次加载。", "warning", 5000);
        await loadCollectionTarget(targetId, { preserveDiff: true });
        return;
      }
      const seen = new Set(currentItems.map((item) => item.id));
      detail[key] = [...currentItems, ...(page.items || []).filter((item) => !seen.has(item.id))];
      renderCollectionDetail(detail);
    } catch (error) {
      toast(`加载历史失败：${error.message}`, "error", 7000);
    } finally {
      state.collectionBusy = false;
      scheduleCollectionPoll();
    }
    return;
  }
  const targetId = node.dataset.targetId || state.selectedCollectionTargetId;
  const path = action === "retry"
    ? `/pldr-api/v1/collection/runs/${encodeURIComponent(node.dataset.runId)}/retry`
    : `/pldr-api/v1/collection/targets/${encodeURIComponent(targetId)}/${action}`;
  state.collectionBusy = true;
  node.disabled = true;
  let actionSucceeded = false;
  try {
    const result = await api(path, { method: "POST" });
    actionSucceeded = true;
    const run = result.run || result;
    if (run.status === "failed") {
      toast(`运行失败：${collectionRunError(run) || "未知错误"}`, "error", 7000);
    } else if (action === "run" && result.created === false) {
      toast("该来源已有排队或运行中的任务，本次未重复创建。", "warning", 5000);
    } else {
      toast(action === "pause" ? "已暂停周期运行。" : action === "resume" ? "已恢复周期运行。" : "采集运行已记录。", "success");
    }
  } catch (error) {
    toast(`来源操作失败：${error.message}`, "error", 7000);
  } finally {
    state.collectionBusy = false;
    await refreshCollectionData(targetId);
    try {
      await refreshData({ keepSelection: true, quiet: true });
    } catch (error) {
      if (actionSucceeded) {
        toast(`来源操作已完成，但专题指标刷新失败：${error.message}`, "warning", 7000);
      }
    }
  }
}

async function copyEventSummary() {
  if (!state.selectedEvent) return;
  const event = state.selectedEvent;
  const assessment = event.assessment?.judgement ? `\n当前判断：${event.assessment.judgement}` : "";
  const text = `${event.title}\n${event.summary}\n文档 ${event.document_count}，独立来源 ${event.independent_source_count}，置信度 ${percent(event.confidence)}${assessment}`;
  try {
    await navigator.clipboard.writeText(text);
    toast("事件摘要已复制。", "success");
  } catch {
    toast("浏览器未授予剪贴板权限。", "error");
  }
}

function clearFilters() {
  $("#search").value = "";
  $("#importance-filter").value = "";
  $("#language-filter").value = "";
  $("#contested-filter").checked = false;
  applyFilters();
}

async function refreshData({
  keepSelection = true,
  quiet = false,
  preferredEventId = null,
  syncSelectionUrl = null,
} = {}) {
  const previousSelection = keepSelection ? state.selectedId : null;
  if (!quiet) setBusy(true, "正在刷新专题");
  try {
    const [overview, sources, config, intakeList, collectionSummary] = await Promise.all([
      api("/pldr-api/v1/overview"),
      api("/pldr-api/v1/sources/health"),
      api("/pldr-api/v1/config").catch(() => null),
      api("/pldr-api/v1/intake?limit=200").catch(() => ({ items: [] })),
      api("/pldr-api/v1/collection/summary").catch(() => null),
    ]);
    state.overview = overview;
    state.events = overview.events || [];
    state.sources = sources.items || [];
    state.config = config;
    state.collectionSummary = collectionSummary;
    renderSearchProvider();
    state.intakeItems = intakeList.items || [];
    if (!state.selectedIntakeId) {
      state.selectedIntakeId = state.intakeItems.find((item) => item.status === "candidate_ready")?.id || null;
    }
    renderIntakeList();
    renderTopic();
    renderMetrics();
    renderSources();
    applyFilters();

    const preferredSelection = preferredEventId
      && state.events.some((event) => event.id === preferredEventId)
      ? preferredEventId
      : null;
    const target = previousSelection && state.events.some((event) => event.id === previousSelection)
      ? previousSelection
      : preferredSelection || state.events[0]?.id;
    const shouldSyncUrl = syncSelectionUrl
      ?? new URL(window.location.href).searchParams.has("event");
    if (target) await selectEvent(target, { open: false, syncUrl: shouldSyncUrl });
    else {
      state.selectedId = null;
      state.selectedEvent = null;
      renderAssessment();
      renderGaps();
    }

    $("#system-state-text").textContent = config?.model_configured
      ? "证据链已连接 · 模型已配置"
      : "证据链已连接 · 确定性降级";
    if (!quiet) toast("专题数据已刷新。", "success", 2600);
  } catch (error) {
    $("#system-state-text").textContent = "连接异常";
    toast(`专题加载失败：${error.message}`, "error", 7000);
    throw error;
  } finally {
    if (!quiet) setBusy(false);
  }
}

function bindEvents() {
  $("#search").addEventListener("input", applyFilters);
  $("#importance-filter").addEventListener("change", applyFilters);
  $("#language-filter").addEventListener("change", applyFilters);
  $("#contested-filter").addEventListener("change", applyFilters);
  $("#btn-refresh").addEventListener("click", () => refreshData());
  $("#btn-report").addEventListener("click", () => generateReport());
  $("#btn-collection").addEventListener("click", openCollectionModal);
  $("#btn-search").addEventListener("click", openExternalSearchModal);
  $("#btn-import").addEventListener("click", openImportModal);
  $("#btn-intake").addEventListener("click", () => openIntakeModal());
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  $("#drawer-report").addEventListener("click", () => generateReport());
  $("#drawer-copy").addEventListener("click", copyEventSummary);
  $("#import-close").addEventListener("click", closeImportModal);
  $("#import-cancel").addEventListener("click", closeImportModal);
  $("#import-form").addEventListener("submit", submitImport);
  $("#search-close").addEventListener("click", closeExternalSearchModal);
  $("#search-form").addEventListener("submit", submitExternalSearch);
  $("#search-select").addEventListener("click", submitSelectedSearchResults);
  $("#collection-close").addEventListener("click", closeCollectionModal);
  $("#collection-refresh").addEventListener("click", () => refreshCollectionData());
  $("#collection-source-form").addEventListener("submit", submitCollectionTarget);
  $("#intake-close").addEventListener("click", closeIntakeModal);

  document.addEventListener("click", (event) => {
    const collectionTarget = event.target.closest("[data-collection-target]");
    if (collectionTarget) {
      loadCollectionTarget(collectionTarget.dataset.collectionTarget);
      return;
    }
    const collectionAction = event.target.closest("[data-collection-action]");
    if (collectionAction) {
      handleCollectionAction(collectionAction.dataset.collectionAction, collectionAction);
      return;
    }
    const searchRetry = event.target.closest("[data-search-retry]");
    if (searchRetry) {
      retryExternalSearchResult(searchRetry.dataset.searchRetry);
      return;
    }
    const intakeNode = event.target.closest("[data-intake-id]");
    if (intakeNode) {
      state.selectedIntakeId = intakeNode.dataset.intakeId;
      renderIntakeList();
      renderIntakeDetail(selectedIntakeItem());
      return;
    }
    const intakeAction = event.target.closest("[data-intake-action]");
    if (intakeAction) {
      handleIntakeAction(intakeAction.dataset.intakeAction, event);
      return;
    }
    const eventNode = event.target.closest("[data-event-id]");
    if (eventNode) {
      selectEvent(eventNode.dataset.eventId, { open: true });
      return;
    }
    const tab = event.target.closest(".drawer-tab");
    if (tab) {
      state.drawerTab = tab.dataset.tab;
      renderDrawer();
      return;
    }
    const importTab = event.target.closest(".import-tab");
    if (importTab) {
      setImportMode(importTab.dataset.mode);
      return;
    }
    if (event.target.closest('[data-action="clear-filters"]')) clearFilters();
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
      event.preventDefault();
      $("#search").focus();
    }
    if (event.key === "Escape") {
      if ($("#event-drawer").classList.contains("open")) closeDrawer();
      else if ($("#collection-modal").open) closeCollectionModal();
      else if ($("#search-modal").open) closeExternalSearchModal();
      else if ($("#intake-modal").open) closeIntakeModal();
      else if ($("#import-modal").open) closeImportModal();
    }
    const card = event.target.closest?.(".event-card");
    if (card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectEvent(card.dataset.eventId, { open: true });
    }
  });
}

async function init() {
  bindEvents();
  try {
    const requestedEvent = new URLSearchParams(window.location.search).get("event");
    await refreshData({
      keepSelection: false,
      quiet: true,
      preferredEventId: requestedEvent,
      syncSelectionUrl: false,
    });
    if (requestedEvent && state.selectedId === requestedEvent) {
      openDrawer();
    } else if (requestedEvent) {
      const url = new URL(window.location.href);
      url.searchParams.delete("event");
      history.replaceState(null, "", url);
    }
  } catch {
    $(".workspace").innerHTML = `
      <section class="fatal-state">
        <span>!</span>
        <h1>PLDR 数据连接失败</h1>
        <p>请确认后端服务已经启动，再刷新页面。</p>
        <button class="btn btn-primary" type="button" onclick="location.reload()">重新连接</button>
      </section>`;
  }
}

init();
