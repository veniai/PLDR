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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
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
    const detail = payload?.detail || payload?.message || `HTTP ${response.status}`;
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
  const items = [
    ["events", metrics.events ?? 0, "事件"],
    ["documents", metrics.documents ?? 0, "文档"],
    ["independence", metrics.independence_groups ?? 0, "独立源组"],
    ["contested", metrics.contested_claims ?? 0, "争议主张"],
  ];
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
            <span>${escapeHtml(claim.origin)}</span>
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
              <a href="${escapeHtml(withEventContext(evidence.document.snapshot_url, event.id))}" target="_blank" rel="noopener">查看证据快照 ↗</a>
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
              <h3>${escapeHtml(document.title)}</h3>
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
  $("#import-url-label").textContent = mode === "rss" ? "RSS / Atom 地址" : "公开网页地址";
  $("#import-url").placeholder = mode === "rss" ? "https://example.org/feed.xml" : "https://example.org/article";
  $("#import-title-field").hidden = mode === "rss";
}

async function submitImport(event) {
  event.preventDefault();
  const url = $("#import-url").value.trim();
  const sourceName = $("#import-source").value.trim();
  const language = $("#import-language").value;
  const title = $("#import-title").value.trim();
  const submit = $("#import-submit");
  submit.disabled = true;
  submit.textContent = "正在抓取";
  $("#import-result").className = "import-result";
  $("#import-result").textContent = "正在校验地址并提取正文…";

  try {
    const endpoint = state.importMode === "rss"
      ? "/pldr-api/v1/import/rss"
      : "/pldr-api/v1/import/url";
    const body = state.importMode === "rss"
      ? { url, source_name: sourceName || "Imported RSS", language }
      : { url, source_name: sourceName || null, title: title || null, language };
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    const count = state.importMode === "rss" ? result.count : 1;
    $("#import-result").className = "import-result success";
    $("#import-result").textContent = `已入库 ${count} 篇资料。当前资料等待人工归并到事件。`;
    toast(`资料导入成功：${count} 篇`, "success");
    await refreshData({ keepSelection: true, quiet: true });
  } catch (error) {
    $("#import-result").className = "import-result error";
    $("#import-result").textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "抓取并入库";
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
    const [overview, sources, config] = await Promise.all([
      api("/pldr-api/v1/overview"),
      api("/pldr-api/v1/sources/health"),
      api("/pldr-api/v1/config").catch(() => null),
    ]);
    state.overview = overview;
    state.events = overview.events || [];
    state.sources = sources.items || [];
    state.config = config;
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
  $("#btn-import").addEventListener("click", openImportModal);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  $("#drawer-report").addEventListener("click", () => generateReport());
  $("#drawer-copy").addEventListener("click", copyEventSummary);
  $("#import-close").addEventListener("click", closeImportModal);
  $("#import-cancel").addEventListener("click", closeImportModal);
  $("#import-form").addEventListener("submit", submitImport);

  document.addEventListener("click", (event) => {
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
