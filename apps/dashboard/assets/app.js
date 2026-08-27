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
  inputType: { web: "公共网页", text: "粘贴文本", file: "本地文件", rss: "RSS" },
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
  const intake = state.overview?.intake || {};
  const items = [
    ["events", metrics.events ?? 0, "事件"],
    ["documents", metrics.documents ?? 0, "文档"],
    ["independence", metrics.independence_groups ?? 0, "独立源组"],
    ["contested", metrics.contested_claims ?? 0, "争议主张"],
    ["intake", intake.candidate_ready ?? 0, "待审材料"],
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
  $("#import-url-label").textContent = mode === "rss" ? "RSS / Atom 地址" : "公开网页地址";
  $("#import-url").placeholder = mode === "rss" ? "https://example.org/feed.xml" : "https://example.org/article";
  $("#import-url").required = isUrlMode;
  $("#import-url-field").hidden = !isUrlMode;
  $("#import-text-field").hidden = mode !== "text";
  $("#import-file-field").hidden = mode !== "file";
  $("#import-title-field").hidden = mode === "rss" || mode === "file";
  $("#import-published-field").hidden = mode !== "text";
  $("#import-source-label").textContent = mode === "url" || mode === "rss" ? "来源说明" : "来源说明（必填）";
  $("#import-source").required = !isUrlMode;
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
  return item.title || item.source?.description || item.file?.name || `${LABELS.inputType[item.input_type] || item.input_type}材料`;
}

function candidateList(item, type) {
  return (item.candidates || []).filter((candidate) => candidate.object_type === type);
}

function renderIntakeList() {
  const root = $("#intake-list");
  if (!root) return;
  const activeCount = state.intakeItems.filter((item) => ["parsed", "candidate_ready", "generation_failed"].includes(item.status)).length;
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
    </article>
    ${renderIntakeFacts(item)}
    ${renderIntakeSnapshots(item)}
    ${machineCandidates ? `<section class="candidate-stack"><h3>机器候选保留</h3>${machineCandidates}</section>` : ""}
    ${item.status === "confirmed" ? renderConfirmedRecord(item, final) : ""}
    ${item.rejection_reason ? `<p class="validation-error">驳回原因：${escapeHtml(item.rejection_reason)}</p>` : ""}
  `;
}

function renderIntakeFacts(item) {
  return `
    <dl class="intake-facts">
      <div><dt>输入类型</dt><dd>${escapeHtml(LABELS.inputType[item.input_type] || item.input_type)}</dd></div>
      <div><dt>来源说明</dt><dd>${escapeHtml(item.source?.description || "未知来源")}</dd></div>
      <div><dt>原始地址</dt><dd>${escapeHtml(item.source?.canonical_url || item.source?.url || "未知地址")}</dd></div>
      <div><dt>标题</dt><dd>${escapeHtml(item.title || "未知标题")}</dd></div>
      <div><dt>发布时间</dt><dd>${formatDate(item.published_at, true)}</dd></div>
      <div><dt>材料指纹</dt><dd>${escapeHtml(item.material?.extracted_hash || "未生成")}</dd></div>
      ${item.file?.name ? `<div><dt>文件</dt><dd>${escapeHtml(item.file.name)} · ${escapeHtml(item.file.media_type)} · ${item.file.size_bytes || 0} bytes</dd></div>` : ""}
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
        <a href="/snapshots/${escapeHtml(final.document)}" target="_blank" rel="noopener">打开正式快照</a>
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

async function refreshIntakeData() {
  const [list, options] = await Promise.all([
    api("/pldr-api/v1/intake?limit=200"),
    api("/pldr-api/v1/intake/options"),
  ]);
  state.intakeItems = list.items || [];
  state.intakeOptions = options || { events: [], entities: [] };
  renderIntakeList();
  renderIntakeDetail(state.intakeItems.find((item) => item.id === state.selectedIntakeId) || state.intakeItems[0]);
}

async function openIntakeModal(itemId = null, quiet = false) {
  const modal = $("#intake-modal");
  if (!quiet && typeof modal.showModal === "function") modal.showModal();
  else if (!quiet) modal.setAttribute("open", "");
  await refreshIntakeData();
  const target = itemId || state.selectedIntakeId || state.intakeItems.find((item) => item.status === "candidate_ready")?.id || state.intakeItems[0]?.id;
  state.selectedIntakeId = target || null;
  renderIntakeList();
  renderIntakeDetail(state.intakeItems.find((item) => item.id === target));
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
      await refreshIntakeData();
      renderIntakeDetail(selectedIntakeItem());
    } catch (error) {
      toast(`候选重新生成失败：${error.message}`, "error", 7000);
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
      await refreshIntakeData();
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
    await refreshIntakeData();
  } catch (error) {
    toast(`采集箱操作失败：${error.message}`, "error", 7000);
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
    const [overview, sources, config, intakeList] = await Promise.all([
      api("/pldr-api/v1/overview"),
      api("/pldr-api/v1/sources/health"),
      api("/pldr-api/v1/config").catch(() => null),
      api("/pldr-api/v1/intake?limit=200").catch(() => ({ items: [] })),
    ]);
    state.overview = overview;
    state.events = overview.events || [];
    state.sources = sources.items || [];
    state.config = config;
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
  $("#btn-import").addEventListener("click", openImportModal);
  $("#btn-intake").addEventListener("click", () => openIntakeModal());
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  $("#drawer-report").addEventListener("click", () => generateReport());
  $("#drawer-copy").addEventListener("click", copyEventSummary);
  $("#import-close").addEventListener("click", closeImportModal);
  $("#import-cancel").addEventListener("click", closeImportModal);
  $("#import-form").addEventListener("submit", submitImport);
  $("#intake-close").addEventListener("click", closeIntakeModal);

  document.addEventListener("click", (event) => {
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
