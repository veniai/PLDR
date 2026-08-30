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
  investigations: [],
  investigationMode: "loading",
  investigationError: "",
  activeInvestigationId: null,
  activeInvestigationTab: "today",
  investigationDetails: new Map(),
  investigationTasks: new Map(),
  investigationActivities: new Map(),
  investigationLinks: new Map(),
  investigationEventDetails: new Map(),
  investigationPollTimer: null,
  investigationRequestSerial: 0,
  localInvestigationState: null,
  pendingCollectionInvestigationId: null,
  reportHistory: new Map(),
  loading: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const API_ROUTES = Object.freeze({
  investigations: "/pldr-api/v1/investigations",
  investigation: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}`,
  investigationTasks: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/tasks`,
  investigationActivity: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/activity`,
  investigationLinks: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/links`,
  taskRetry: (id) => `/pldr-api/v1/tasks/${encodeURIComponent(id)}/retry`,
  search: "/pldr-api/v1/search",
  searchSelect: "/pldr-api/v1/search/select",
  reports: "/pldr-api/v1/reports",
});

const LOCAL_INVESTIGATION_KEY = "pldr.investigations.v1";
const NEW_INVESTIGATION_VALUE = "__new_investigation__";
const UNASSIGNED_VALUE = "__unassigned__";
const INVESTIGATION_TABS = new Set(["today", "discovery", "monitoring", "review", "events", "claims", "reports", "activity"]);

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
    queued: "已排队",
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
  investigationStatus: { active: "进行中", paused: "已暂停", closed: "已关闭", archived: "已归档" },
  taskStage: {
    queued: "queued · 已排队",
    waiting: "queued · 已排队",
    fetching: "fetching · 正在抓取",
    running: "fetching · 正在抓取",
    generating: "generating · 正在生成候选",
    parsed: "generating · 正在生成候选",
    ready: "ready · 等待人工审核",
    candidate_ready: "ready · 等待人工审核",
    failed: "failed · 处理失败",
    blocked: "failed · 需要恢复",
    retrying: "queued · 已提交重试",
    completed: "completed · 已处理",
    confirmed: "accepted · 已确认入档",
    accepted: "accepted · 已确认入档",
    rejected: "rejected · 已驳回",
    cancelled: "cancelled · 已撤销",
  },
};

const ACTIVITY_ACTION_LABELS = Object.freeze({
  "investigation.created": "创建了专题",
  "investigation.updated": "更新了专题信息",
  "migration.classified": "系统完成既有资料归类",
  "migration.task_backfilled": "系统补齐既有待办",
  "search.query_linked": "把检索关联到专题",
  "search.query_completed": "公开资料检索完成",
  "search.query_failed": "公开资料检索失败",
  "search.result_selected": "选择资料进入处理队列",
  "collection.target_linked": "添加了固定监测来源",
  "collection.version_linked": "监测版本进入待审流程",
  "event.linked_from_confirmation": "确认候选并关联正式事件",
  "report.generated": "生成了专题报告",
  "object.linked": "关联了专题资料",
  "task.queued": "处理任务已排队",
  "task.fetching": "开始抓取原始资料",
  "task.generating": "开始生成审核候选",
  "task.ready": "候选已就绪，等待人工审核",
  "task.failed": "处理任务失败",
  "task.confirmed": "人工确认任务已完成",
  "task.rejected": "人工驳回任务已完成",
  "task.deduplicated": "复用了已有处理任务",
  "task.reused": "复用了已有处理任务",
  "task.lease_recovered": "超时任务已恢复到队列",
  "task.retry": "重新提交了失败任务",
  "intake.confirmed": "人工确认材料入档",
  "intake.rejected": "人工驳回材料",
  "intake.cancelled": "撤销材料处理",
  investigation_created_locally: "创建了浏览器本地专题草稿",
  linked_locally: "在本浏览器关联了资料",
});

const ACTIVITY_DETAIL_LABELS = Object.freeze({
  title: "专题",
  question: "核心问题",
  description: "说明",
  status: "状态",
  keyword: "关键词",
  scope: "范围",
  result_count: "找到结果",
  latency_ms: "耗时",
  reason: "原因",
  error: "错误",
  error_message: "错误",
  error_class: "错误类型",
  attempt_number: "第几次处理",
  from_status: "原状态",
  to_status: "新状态",
  intake_status: "材料状态",
  task_status: "任务状态",
  event_count: "事件",
  evidence_count: "证据",
  filename: "报告文件",
  outcome: "处理结果",
  classification: "归类依据",
  role: "关联角色",
  retry_model: "重新调用模型",
  manual_link: "手动关联",
  legacy_api: "来自兼容接口",
  target_id: "监测来源",
  run_id: "采集运行",
  requested_url: "原始网址",
});

const SEARCH_TRACE_OUTCOME_LABELS = Object.freeze({
  queued: "已排队",
  ready: "候选已就绪",
  failed: "处理失败",
  linked: "已关联既有材料",
  linked_existing_intake: "已关联既有材料",
  added: "已加入采集箱",
  retry_succeeded: "重试成功，候选已就绪",
  retry_failed: "重试失败",
  retry_not_needed: "无需重试",
});

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
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    error.payload = payload;
    throw error;
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

function isUnsupportedEndpoint(error) {
  return [404, 405, 501].includes(Number(error?.status));
}

function localInvestigationDefaults() {
  return { investigations: [], links: {}, activities: {}, reports: {} };
}

function loadLocalInvestigationState() {
  if (state.localInvestigationState) return state.localInvestigationState;
  try {
    const parsed = JSON.parse(localStorage.getItem(LOCAL_INVESTIGATION_KEY) || "null");
    state.localInvestigationState = {
      ...localInvestigationDefaults(),
      ...(parsed && typeof parsed === "object" ? parsed : {}),
    };
  } catch {
    state.localInvestigationState = localInvestigationDefaults();
  }
  return state.localInvestigationState;
}

function saveLocalInvestigationState() {
  try {
    localStorage.setItem(LOCAL_INVESTIGATION_KEY, JSON.stringify(loadLocalInvestigationState()));
    return true;
  } catch (error) {
    toast(`浏览器无法保存专题草稿：${error.message}`, "error", 7000);
    return false;
  }
}

function makeClientId(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function unwrapItems(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

function normalizeInvestigation(raw, syncMode = "server") {
  const source = raw?.investigation || raw || {};
  const id = String(source.id || source.investigation_id || makeClientId("investigation"));
  const declaredKind = String(source.kind || source.investigation_kind || "").toLowerCase();
  const kind = id === "inv_unclassified" || declaredKind === "system"
    ? "system"
    : id === "inv_demo_suez_2021" || declaredKind === "demo"
      ? "demo"
      : syncMode === "compatibility"
        ? "compatibility"
        : "user";
  const normalizedSyncMode = kind === "system"
    ? "system"
    : kind === "demo"
      ? "demo"
      : kind === "compatibility"
        ? "compatibility"
        : source.sync_mode || syncMode;
  return {
    ...source,
    id,
    title: source.title || source.name || "未命名专题",
    question: source.question || source.objective || source.description || "尚未填写核心调查问题。",
    description: source.description || "",
    status: source.status || "active",
    created_at: source.created_at || null,
    updated_at: source.updated_at || source.last_updated || source.created_at || null,
    kind,
    sync_mode: normalizedSyncMode,
    raw: source,
  };
}

function compatibilityInvestigation() {
  const topic = state.overview?.topic || {};
  return normalizeInvestigation({
    id: "__legacy_overview__",
    title: topic.title || "现有事件与证据",
    question: topic.description || topic.subtitle || "查看当前后端已经确认的事件、主张与证据。",
    description: "这是旧接口数据的兼容视图，不代表这些对象已经建立新的专题关联。",
    status: "active",
    updated_at: state.overview?.last_updated,
    sync_mode: "compatibility",
  }, "compatibility");
}

function activeInvestigation() {
  return state.investigations.find((item) => item.id === state.activeInvestigationId) || null;
}

function unclassifiedInvestigation() {
  return state.investigations.find((item) => item.id === "inv_unclassified" || item.sync_mode === "system") || null;
}

function isServerInvestigation(investigation) {
  return ["server", "system", "demo"].includes(investigation?.sync_mode);
}

function investigationSyncLabel(investigation) {
  if (investigation?.sync_mode === "local") return "本地草稿 · 仅此浏览器";
  if (investigation?.sync_mode === "system") return "系统待归类 · 非用户专题";
  if (investigation?.sync_mode === "demo") return "内置示例 · 非用户专题";
  if (investigation?.sync_mode === "compatibility") return "兼容视图 · 尚未建立专题关联";
  return "服务端持久专题";
}

function recordLocalActivity(investigationId, action, detail, objectType = null, objectId = null) {
  if (!investigationId || investigationId === UNASSIGNED_VALUE || investigationId === "__legacy_overview__") return;
  const local = loadLocalInvestigationState();
  local.activities[investigationId] = local.activities[investigationId] || [];
  local.activities[investigationId].unshift({
    id: makeClientId("activity"),
    action,
    detail,
    object_type: objectType,
    object_id: objectId,
    actor: "本浏览器",
    created_at: new Date().toISOString(),
    local_only: true,
  });
  local.activities[investigationId] = local.activities[investigationId].slice(0, 200);
  saveLocalInvestigationState();
}

function addLocalLink(investigationId, objectType, objectId, metadata = {}) {
  const local = loadLocalInvestigationState();
  local.links[investigationId] = local.links[investigationId] || [];
  const before = [...local.links[investigationId]];
  const exists = local.links[investigationId].some((link) => link.object_type === objectType && link.object_id === objectId);
  if (!exists) {
    local.links[investigationId].push({
      id: makeClientId("link"),
      investigation_id: investigationId,
      object_type: objectType,
      object_id: objectId,
      role: "member",
      metadata,
      created_at: new Date().toISOString(),
      local_only: true,
    });
  }
  if (!saveLocalInvestigationState()) {
    local.links[investigationId] = before;
    throw new Error("浏览器未能保存本地专题关联");
  }
  state.investigationLinks.set(investigationId, [...local.links[investigationId]]);
  recordLocalActivity(investigationId, "linked_locally", `已在本浏览器关联 ${objectType} ${objectId}`, objectType, objectId);
}

function createLocalInvestigation(fields) {
  const now = new Date().toISOString();
  const local = loadLocalInvestigationState();
  const investigation = normalizeInvestigation({
    id: makeClientId("local-investigation"),
    title: fields.title,
    question: fields.question || fields.title,
    description: fields.description || "",
    status: "active",
    created_at: now,
    updated_at: now,
    sync_mode: "local",
  }, "local");
  local.investigations.unshift(investigation);
  if (!saveLocalInvestigationState()) {
    local.investigations = local.investigations.filter((item) => item.id !== investigation.id);
    throw new Error("浏览器未能保存本地专题草稿");
  }
  state.investigations = [investigation, ...state.investigations.filter((item) => item.id !== "__legacy_overview__")];
  recordLocalActivity(investigation.id, "investigation_created_locally", "专题草稿已创建，但尚未同步到服务端", "investigation", investigation.id);
  return investigation;
}

async function createInvestigation(fields) {
  const body = {
    title: fields.title.trim(),
    question: (fields.question || fields.title).trim(),
    description: (fields.description || "").trim(),
    status: "active",
  };
  if (state.investigationMode !== "unavailable") {
    try {
      const payload = await api(API_ROUTES.investigations, { method: "POST", body: JSON.stringify(body) });
      const investigation = normalizeInvestigation(payload.investigation || payload, "server");
      state.investigationMode = "server";
      state.investigationError = "";
      state.investigations = [investigation, ...state.investigations.filter((item) => item.id !== investigation.id && item.id !== "__legacy_overview__")];
      return investigation;
    } catch (error) {
      if (!isUnsupportedEndpoint(error)) throw error;
      state.investigationMode = "unavailable";
      state.investigationError = "当前后端没有专题接口";
    }
  }
  return createLocalInvestigation(body);
}

async function associateInvestigationObjects(investigation, objectType, objectIds, metadata = {}) {
  const ids = [...new Set((objectIds || []).filter(Boolean))];
  if (!investigation || !ids.length || investigation.sync_mode === "compatibility") {
    return { linked: 0, mode: "unassigned", failed: ids.length };
  }
  if (investigation.sync_mode === "local") {
    ids.forEach((id) => addLocalLink(investigation.id, objectType, id, metadata));
    return { linked: ids.length, mode: "local", failed: 0 };
  }
  const outcomes = await Promise.allSettled(ids.map((objectId) => api(API_ROUTES.investigationLinks(investigation.id), {
    method: "POST",
    body: JSON.stringify({ object_type: objectType, object_id: objectId, role: "member", actor: "analyst" }),
  })));
  const linked = outcomes.filter((outcome) => outcome.status === "fulfilled").length;
  return {
    linked,
    failed: ids.length - linked,
    mode: "server",
    errors: outcomes.filter((outcome) => outcome.status === "rejected").map((outcome) => outcome.reason?.message || "关联失败"),
  };
}

function taskStageFromIntake(item) {
  return ({
    parsed: "generating",
    candidate_ready: "ready",
    generation_failed: "failed",
    failed: "failed",
    confirmed: "accepted",
    rejected: "rejected",
    cancelled: "cancelled",
  })[item?.status] || item?.status || "queued";
}

function canonicalTaskStage(task) {
  const explicit = task?.stage || task?.state || task?.status || task?.intake_status;
  if (!explicit) {
    const intake = state.intakeItems.find((item) => item.id === (task?.intake_item_id || task?.intake?.id));
    if (intake) return taskStageFromIntake(intake);
  }
  const raw = String(explicit || "queued").toLowerCase();
  return ({ waiting: "queued", pending: "queued", running: "fetching", processing: "fetching", parsed: "generating", candidate_ready: "ready", generation_failed: "failed", succeeded: "completed", confirmed: "accepted" })[raw] || raw;
}

function taskIntakeId(task) {
  return task?.intake_item_id || task?.intake?.id || task?.result?.intake_item_id || null;
}

function taskTitle(task) {
  const intakeId = taskIntakeId(task);
  const intake = state.intakeItems.find((item) => item.id === intakeId);
  const resultId = task?.subject?.id || task?.subject_id || task?.payload?.result_id || task?.payload_json?.result_id;
  const searchResult = state.searchResults.find((item) => item.id === resultId);
  return task?.title
    || task?.payload?.title
    || task?.payload_json?.title
    || task?.result?.title
    || searchResult?.title
    || (intake ? intakeTitle(intake) : null)
    || task?.subject_title
    || task?.payload?.requested_url
    || resultId
    || "处理中资料";
}

function taskError(task) {
  const error = task?.error;
  return task?.error_message || (typeof error === "object" ? error?.message || error?.class : error) || task?.last_error || "";
}

function taskStatusMarkup(stage) {
  const safe = canonicalTaskStage({ status: stage });
  return `<span class="task-stage ${escapeHtml(safe)}">${escapeHtml(LABELS.taskStage[safe] || safe)}</span>`;
}

function linksForInvestigation(investigationId) {
  const explicit = state.investigationLinks.get(investigationId) || [];
  const detail = state.investigationDetails.get(investigationId) || {};
  const embedded = unwrapItems(detail, "links", "linked_objects", "memberships");
  const local = loadLocalInvestigationState().links[investigationId] || [];
  const seen = new Set();
  return [...explicit, ...embedded, ...local].filter((link) => {
    const key = `${link.object_type || link.type}:${link.object_id || link.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function linkedIds(investigation, objectType) {
  if (!investigation) return [];
  if (investigation.sync_mode === "compatibility") {
    if (objectType === "event") return state.events.map((item) => item.id);
    if (objectType === "intake") return state.intakeItems.map((item) => item.id);
    if (objectType === "collection_target") return state.collectionTargets.map((item) => item.id);
  }
  const detail = state.investigationDetails.get(investigation.id) || investigation.raw || {};
  const plural = { event: "events", intake: "intake_items", collection_target: "collection_targets" }[objectType];
  const directIds = [
    ...(Array.isArray(detail[`${objectType}_ids`]) ? detail[`${objectType}_ids`] : []),
    ...(Array.isArray(detail[plural]) ? detail[plural].map((item) => typeof item === "string" ? item : item.id) : []),
  ];
  const allLinks = linksForInvestigation(investigation.id);
  const linkIds = allLinks
    .filter((link) => (link.object_type || link.type) === objectType)
    .map((link) => link.object_id || link.object?.id);
  const derivedEventIds = objectType === "event" ? state.intakeItems
    .filter((item) => allLinks.some((link) => (link.object_type || link.type) === "intake" && (link.object_id || link.object?.id) === item.id))
    .map((item) => item.final_object_ids?.event || item.confirmation_result?.formal_object_ids?.event || item.final_event_id)
    .filter(Boolean) : [];
  return [...new Set([...directIds, ...linkIds, ...derivedEventIds].filter(Boolean))];
}

function eventsForInvestigation(investigation) {
  const ids = new Set(linkedIds(investigation, "event"));
  const detail = state.investigationDetails.get(investigation?.id) || {};
  const embedded = Array.isArray(detail.events) ? detail.events.filter((item) => typeof item === "object") : [];
  const combined = [...state.events, ...embedded];
  const seen = new Set();
  return combined.filter((event) => ids.has(event.id) && !seen.has(event.id) && seen.add(event.id));
}

function targetsForInvestigation(investigation) {
  const ids = new Set(linkedIds(investigation, "collection_target"));
  return state.collectionTargets.filter((target) => ids.has(target.id));
}

function tasksForInvestigation(investigation) {
  if (!investigation) return [];
  const serverTasks = state.investigationTasks.get(investigation.id) || [];
  const detail = state.investigationDetails.get(investigation.id) || {};
  const embedded = unwrapItems(detail, "tasks", "review_tasks");
  const intakeIds = new Set(linkedIds(investigation, "intake"));
  const targetIds = new Set(linkedIds(investigation, "collection_target"));
  if (targetIds.size) {
    state.intakeItems.forEach((item) => {
      const targetId = item.collection?.target_id || item.review?.collection?.target_id || item.review?.collection?.target?.id;
      if (targetIds.has(targetId)) intakeIds.add(item.id);
    });
  }
  if (investigation.sync_mode === "compatibility") {
    state.intakeItems.forEach((item) => intakeIds.add(item.id));
  }
  const derived = state.intakeItems.filter((item) => intakeIds.has(item.id)).map((item) => ({
    id: `intake:${item.id}`,
    investigation_id: investigation.id,
    intake_item_id: item.id,
    status: taskStageFromIntake(item),
    title: intakeTitle(item),
    error_message: item.error || item.candidate_generation?.error || "",
    created_at: item.created_at,
    updated_at: item.updated_at || item.reviewed_at || item.created_at,
    derived_from: "intake",
  }));
  const byKey = new Map();
  [...derived, ...embedded, ...serverTasks].forEach((task) => {
    const key = taskIntakeId(task) ? `intake:${taskIntakeId(task)}` : task.id || task.task_id || makeClientId("task");
    byKey.set(key, { ...byKey.get(key), ...task, id: task.id || task.task_id || key });
  });
  return [...byKey.values()].sort((a, b) => {
    const rank = { failed: 0, ready: 1, fetching: 2, generating: 3, queued: 4, accepted: 8, rejected: 9, cancelled: 10, completed: 11 };
    return (rank[canonicalTaskStage(a)] ?? 7) - (rank[canonicalTaskStage(b)] ?? 7)
      || new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0);
  });
}

function taskIsActive(task) {
  return ["queued", "fetching", "generating", "ready", "failed"].includes(canonicalTaskStage(task));
}

function investigationMetrics(investigation) {
  const raw = investigation?.metrics || investigation?.counts || investigation?.raw?.metrics || investigation?.raw?.counts || {};
  const tasks = tasksForInvestigation(investigation);
  const taskStatus = investigation?.task_status || investigation?.raw?.task_status || {};
  const statusActive = ["queued", "fetching", "generating", "ready", "failed"].reduce((sum, key) => sum + Number(taskStatus[key] || 0), 0);
  return {
    tasks: Number(raw.pending_tasks ?? (Object.keys(taskStatus).length ? statusActive : tasks.filter(taskIsActive).length)),
    ready: Number(raw.ready ?? raw.review_ready ?? taskStatus.ready ?? tasks.filter((task) => canonicalTaskStage(task) === "ready").length),
    events: Number(raw.events ?? raw.event_count ?? eventsForInvestigation(investigation).length),
    sources: Number(raw.sources ?? raw.collection_targets ?? raw.source_count ?? targetsForInvestigation(investigation).length),
  };
}

function allHomeAssignments() {
  const assignments = [];
  const seenIntake = new Set();
  state.investigations.forEach((investigation) => {
    if (investigation.sync_mode === "demo") return;
    if (investigation.sync_mode === "compatibility" && state.investigations.length > 1) return;
    tasksForInvestigation(investigation).filter(taskIsActive).forEach((task) => {
      const intakeId = taskIntakeId(task);
      if (intakeId) seenIntake.add(intakeId);
      assignments.push({ task, investigation });
    });
  });
  state.intakeItems.filter((item) => ["queued", "parsed", "candidate_ready", "generation_failed", "failed"].includes(item.status) && !seenIntake.has(item.id)).forEach((item) => {
    assignments.push({
      task: { id: `unassigned:${item.id}`, intake_item_id: item.id, status: taskStageFromIntake(item), title: intakeTitle(item), error_message: item.error, created_at: item.created_at },
      investigation: null,
    });
  });
  return assignments.slice(0, 30);
}

function renderDestinationPickers(preferredId = state.activeInvestigationId) {
  const candidates = state.investigations.filter((item) => !["compatibility", "system", "demo"].includes(item.sync_mode));
  const options = [
    `<option value="${UNASSIGNED_VALUE}">暂不归入明确专题（系统待归类）</option>`,
    ...candidates.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}${item.sync_mode === "local" ? " · 本地草稿" : ""}</option>`),
    `<option value="${NEW_INVESTIGATION_VALUE}">＋ 新建专题</option>`,
  ].join("");
  ["search", "import"].forEach((kind) => {
    const select = $(`#${kind}-destination`);
    if (!select) return;
    const previous = select.value;
    select.innerHTML = options;
    const desired = [preferredId, previous].find((value) => value && [...select.options].some((option) => option.value === value));
    select.value = desired || (candidates[0]?.id || NEW_INVESTIGATION_VALUE);
    updateDestinationFields(kind);
  });
}

function updateDestinationFields(kind) {
  const select = $(`#${kind}-destination`);
  const field = $(`#${kind}-new-investigation-field`);
  const input = $(`#${kind}-new-investigation`);
  if (!select || !field || !input) return;
  const isNew = select.value === NEW_INVESTIGATION_VALUE;
  field.hidden = !isNew;
  input.required = isNew && kind === "import";
  input.disabled = !isNew;
  const selected = state.investigations.find((item) => item.id === select.value);
  const note = $(`#${kind}-destination-note`);
  if (note) {
    note.textContent = isNew
      ? (state.investigationMode === "unavailable" ? "专题接口不可用：成功采集后只会建立明确标注的浏览器本地专题草稿。" : "成功提交时会创建持久专题，并把每条处理任务放入该专题。")
      : selected?.sync_mode === "local"
        ? "该专题只保存在本浏览器；真实材料仍保存在服务端采集箱。"
        : select.value === UNASSIGNED_VALUE
          ? (unclassifiedInvestigation()
            ? "检索与材料会进入服务端“系统待归类”（inv_unclassified），异步进度仍可追踪，但不会冒充用户专题。"
            : "旧版后端未返回系统待归类专题；将保留原采集箱兼容流程，不会声称已归入用户专题。")
          : "材料处理状态会在专题“待我审核”中持续显示。";
  }
  if (kind === "search" && state.searchResults.length) renderSearchResults();
}

function destinationIntent(kind) {
  const value = $(`#${kind}-destination`)?.value || UNASSIGNED_VALUE;
  if (value === NEW_INVESTIGATION_VALUE) {
    const title = $(`#${kind}-new-investigation`)?.value.trim() || "";
    if (title.length < 2) throw new Error("请填写至少 2 个字符的新专题名称。");
    return { type: "new", fields: { title, question: title, description: "由资料采集流程创建。" } };
  }
  if (value === UNASSIGNED_VALUE) return { type: "unassigned", investigation: unclassifiedInvestigation() };
  const investigation = state.investigations.find((item) => item.id === value);
  if (!investigation) throw new Error("目标专题已经不存在，请重新选择。");
  return { type: "existing", investigation };
}

function renderInvestigationHome() {
  const note = $("#investigation-sync-note");
  if (state.investigationMode === "server") {
    note.className = "investigation-sync-note";
    note.textContent = "专题、任务与操作记录由服务端持久保存。页面只展示后端实际返回的进度，不会把搜索结果或 AI 候选冒充正式证据。";
  } else if (state.investigationMode === "unavailable") {
    note.className = "investigation-sync-note local";
    note.textContent = "当前后端未提供专题接口。新建专题与归类只保存在此浏览器，并明确标注；原有事件、采集箱、来源监测和证据链仍照常使用。";
  } else if (state.investigationMode === "error") {
    note.className = "investigation-sync-note error";
    note.textContent = `专题服务读取失败：${state.investigationError}。没有伪造同步结果；可继续使用经典事件视图与既有采集箱。`;
  } else {
    note.className = "investigation-sync-note";
    note.textContent = "正在读取专题服务…";
  }

  const userInvestigations = state.investigations.filter((item) => !["system", "demo", "compatibility"].includes(item.sync_mode));
  const referenceInvestigations = state.investigations.filter((item) => ["demo", "compatibility"].includes(item.sync_mode));
  const renderCards = (items) => items.map((investigation) => {
    const metrics = investigationMetrics(investigation);
    return `
      <button class="investigation-card ${escapeHtml(investigation.sync_mode)}" type="button" data-investigation-id="${escapeHtml(investigation.id)}">
        <span class="investigation-card-topline">
          <span class="sync-badge ${escapeHtml(investigation.sync_mode)}">${escapeHtml(investigationSyncLabel(investigation))}</span>
          <span>${escapeHtml(LABELS.investigationStatus[investigation.status] || investigation.status)}</span>
        </span>
        <h3>${escapeHtml(investigation.title)}</h3>
        <p>${escapeHtml(investigation.question)}</p>
        <span class="investigation-card-metrics">
          <span><strong>${metrics.tasks}</strong>待处理</span>
          <span><strong>${metrics.ready}</strong>待审核</span>
          <span><strong>${metrics.events}</strong>已确认事件</span>
        </span>
      </button>`;
  }).join("");
  $("#investigation-count").textContent = String(userInvestigations.length);
  $("#investigation-list").innerHTML = `${userInvestigations.length ? renderCards(userInvestigations) : `
    <div class="investigation-empty investigation-list-empty">
      <strong>还没有专题</strong>
      <p>先创建一个明确的问题空间，再把搜索结果、导入材料与监测来源归入其中。</p>
      <button class="btn btn-primary" type="button" data-investigation-action="create">创建第一个专题</button>
    </div>`}${referenceInvestigations.length ? `<div class="investigation-list-divider"><span>参考入口</span><small>不计入“我的专题”，也不能作为资料归类目标</small></div>${renderCards(referenceInvestigations)}` : ""}`;

  const assignments = allHomeAssignments();
  $("#assignment-count").textContent = String(assignments.length);
  $("#assignment-list").innerHTML = assignments.length ? assignments.map(({ task, investigation }) => {
    const stage = canonicalTaskStage(task);
    return `
      <button class="assignment-card" type="button" data-investigation-assignment="${escapeHtml(task.id)}" data-investigation-id="${escapeHtml(investigation?.id || "")}" data-intake-id="${escapeHtml(taskIntakeId(task) || "")}">
        <span class="assignment-priority ${escapeHtml(stage)}"></span>
        <span class="assignment-copy">
          <h3>${escapeHtml(taskTitle(task))}</h3>
          <span class="assignment-meta"><span>${escapeHtml(investigation?.sync_mode === "system" ? "系统待归类" : investigation?.title || "待归类材料")}</span><span>${formatDate(task.updated_at || task.created_at, true)}</span></span>
          ${taskError(task) ? `<p>${escapeHtml(taskError(task))}</p>` : ""}
          <span class="assignment-meta">${taskStatusMarkup(stage)}</span>
        </span>
        <span class="assignment-go">›</span>
      </button>`;
  }).join("") : '<div class="investigation-empty"><strong>当前没有待处理项</strong><p>这里不会用示例任务填充。新采集、网页变化或失败任务出现后会显示。</p></div>';
  renderDestinationPickers();
  renderMetrics();
}

async function refreshInvestigationDirectory() {
  const local = loadLocalInvestigationState();
  let serverInvestigations = [];
  try {
    const payload = await api(API_ROUTES.investigations);
    serverInvestigations = unwrapItems(payload, "items", "investigations").map((item) => normalizeInvestigation(item, "server"));
    state.investigationMode = "server";
    state.investigationError = "";
    const taskLists = await Promise.allSettled(serverInvestigations.slice(0, 50).map((investigation) => api(API_ROUTES.investigationTasks(investigation.id))));
    taskLists.forEach((result, index) => {
      if (result.status === "fulfilled") state.investigationTasks.set(serverInvestigations[index].id, unwrapItems(result.value, "items", "tasks", "review_tasks"));
    });
  } catch (error) {
    if (isUnsupportedEndpoint(error)) {
      state.investigationMode = "unavailable";
      state.investigationError = error.message;
    } else {
      state.investigationMode = "error";
      state.investigationError = error.message;
    }
  }
  const localInvestigations = (local.investigations || []).map((item) => normalizeInvestigation(item, "local"));
  const combined = [...serverInvestigations, ...localInvestigations];
  state.investigations = serverInvestigations.length ? combined : [...localInvestigations, compatibilityInvestigation()];
  if (!state.collectionTargets.length) {
    try {
      const targets = await api("/pldr-api/v1/collection/targets");
      state.collectionTargets = targets.items || targets.targets || [];
    } catch {
      // Collection is optional for the investigation shell; the existing monitor retains its own error UI.
    }
  }
  renderInvestigationHome();
}

async function loadInvestigationWorkspace(investigationId, { quiet = false } = {}) {
  const investigation = state.investigations.find((item) => item.id === investigationId);
  if (!investigation) return;
  const requestSerial = ++state.investigationRequestSerial;
  if (investigation.sync_mode === "local") {
    state.investigationLinks.set(investigation.id, [...(loadLocalInvestigationState().links[investigation.id] || [])]);
    state.investigationActivities.set(investigation.id, [...(loadLocalInvestigationState().activities[investigation.id] || [])]);
    renderInvestigationPage();
    return;
  }
  if (investigation.sync_mode === "compatibility") {
    renderInvestigationPage();
    return;
  }
  if (!quiet) $("#investigation-panel").innerHTML = '<div class="investigation-empty"><strong>正在读取专题任务与记录…</strong></div>';
  const calls = await Promise.allSettled([
    api(API_ROUTES.investigation(investigation.id)),
    api(API_ROUTES.investigationTasks(investigation.id)),
    api(API_ROUTES.investigationActivity(investigation.id)),
  ]);
  if (requestSerial !== state.investigationRequestSerial || state.activeInvestigationId !== investigation.id) return;
  if (calls[0].status === "fulfilled") {
    const detail = calls[0].value;
    state.investigationDetails.set(investigation.id, detail);
    const updated = normalizeInvestigation(detail.investigation || detail, "server");
    state.investigations = state.investigations.map((item) => item.id === investigation.id ? { ...item, ...updated, raw: { ...item.raw, ...updated.raw } } : item);
  } else if (!quiet) {
    $("#investigation-panel").innerHTML = `<div class="investigation-empty"><strong>专题详情读取失败</strong><p>${escapeHtml(calls[0].reason?.message || "未知错误")}</p><button class="btn btn-ghost" type="button" data-investigation-action="refresh">重试</button></div>`;
    return;
  }
  if (calls[1].status === "fulfilled") state.investigationTasks.set(investigation.id, unwrapItems(calls[1].value, "items", "tasks", "review_tasks"));
  if (calls[2].status === "fulfilled") state.investigationActivities.set(investigation.id, unwrapItems(calls[2].value, "items", "activity", "activities", "decisions"));
  const detailLinks = unwrapItems(state.investigationDetails.get(investigation.id), "links");
  if (detailLinks.length) state.investigationLinks.set(investigation.id, detailLinks);
  renderInvestigationHome();
  renderInvestigationPage();
  scheduleInvestigationPoll();
}

function scheduleInvestigationPoll() {
  if (state.investigationPollTimer) window.clearTimeout(state.investigationPollTimer);
  state.investigationPollTimer = null;
  const investigation = activeInvestigation();
  if (!isServerInvestigation(investigation)) return;
  const hasProcessing = tasksForInvestigation(investigation).some((task) => ["queued", "fetching", "generating"].includes(canonicalTaskStage(task)));
  if (!hasProcessing) return;
  state.investigationPollTimer = window.setTimeout(() => loadInvestigationWorkspace(investigation.id, { quiet: true }), 2500);
}

function setRouteVisibility(route) {
  const classic = route === "classic";
  $("#investigation-app").hidden = classic;
  $("#classic-workspace-shell").hidden = !classic;
  $("#btn-workbench-home").classList.toggle("active", !classic);
  $("#btn-classic-workspace").classList.toggle("active", classic);
}

function showInvestigationHome({ syncUrl = true } = {}) {
  state.activeInvestigationId = null;
  if (state.investigationPollTimer) window.clearTimeout(state.investigationPollTimer);
  state.investigationPollTimer = null;
  setRouteVisibility("investigation");
  $("#investigation-home").hidden = false;
  $("#investigation-detail-page").hidden = true;
  renderInvestigationHome();
  if (syncUrl) {
    const url = new URL(window.location.href);
    ["investigation", "tab", "view", "event"].forEach((key) => url.searchParams.delete(key));
    history.pushState(null, "", url);
  }
}

function showClassicWorkspace({ syncUrl = true } = {}) {
  setRouteVisibility("classic");
  renderMetrics();
  if (syncUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "classic");
    url.searchParams.delete("investigation");
    url.searchParams.delete("tab");
    history.pushState(null, "", url);
  }
}

async function openInvestigation(investigationId, tab = "today", { syncUrl = true } = {}) {
  const investigation = state.investigations.find((item) => item.id === investigationId);
  if (!investigation) {
    toast("专题不存在或已被移除。", "error");
    showInvestigationHome({ syncUrl });
    return;
  }
  state.activeInvestigationId = investigationId;
  state.activeInvestigationTab = INVESTIGATION_TABS.has(tab) ? tab : "today";
  setRouteVisibility("investigation");
  $("#investigation-home").hidden = true;
  $("#investigation-detail-page").hidden = false;
  renderInvestigationPage();
  if (syncUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("investigation", investigationId);
    url.searchParams.set("tab", state.activeInvestigationTab);
    url.searchParams.delete("view");
    url.searchParams.delete("event");
    history.pushState(null, "", url);
  }
  await loadInvestigationWorkspace(investigationId);
  if (state.activeInvestigationTab === "claims") await loadInvestigationEvidence(investigation);
}

function setInvestigationTab(tab, { syncUrl = true } = {}) {
  if (!INVESTIGATION_TABS.has(tab) || !activeInvestigation()) return;
  state.activeInvestigationTab = tab;
  renderInvestigationPage();
  if (syncUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    history.pushState(null, "", url);
  }
  if (tab === "claims") loadInvestigationEvidence(activeInvestigation());
}

function renderInvestigationPage() {
  const investigation = activeInvestigation();
  if (!investigation) return;
  const metrics = investigationMetrics(investigation);
  $("#investigation-page-sync").className = `investigation-sync-chip ${escapeHtml(investigation.sync_mode)}`;
  $("#investigation-page-sync").textContent = investigationSyncLabel(investigation);
  $("#investigation-page-header").innerHTML = `
    <div>
      <span class="eyebrow">ACTIVE INVESTIGATION</span>
      <h1 id="investigation-page-title">${escapeHtml(investigation.title)}</h1>
      <p>${escapeHtml(investigation.question)}${investigation.description ? ` · ${escapeHtml(investigation.description)}` : ""}</p>
    </div>
    <div class="investigation-page-actions">
      <button class="btn btn-ghost" type="button" data-investigation-action="search">⌕ 发现资料</button>
      <button class="btn btn-ghost" type="button" data-investigation-action="import">＋ 导入资料</button>
      <button class="btn btn-primary" type="button" data-investigation-action="review">审核 ${metrics.ready}</button>
    </div>`;
  $$("[data-investigation-tab]", $("#investigation-tabs")).forEach((button) => {
    const active = button.dataset.investigationTab === state.activeInvestigationTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  renderInvestigationPanel(investigation);
  renderMetrics();
}

function renderInvestigationPanel(investigation) {
  const renderers = {
    today: renderInvestigationToday,
    discovery: renderInvestigationDiscovery,
    monitoring: renderInvestigationMonitoring,
    review: renderInvestigationReview,
    events: renderInvestigationEvents,
    claims: renderInvestigationClaims,
    reports: renderInvestigationReports,
    activity: renderInvestigationActivity,
  };
  $("#investigation-panel").innerHTML = (renderers[state.activeInvestigationTab] || renderers.today)(investigation);
}

function investigationPanelHeading(eyebrow, title, description, actions = "") {
  return `<div class="investigation-panel-heading"><div><span class="panel-kicker">${escapeHtml(eyebrow)}</span><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div>${actions ? `<div class="investigation-panel-actions">${actions}</div>` : ""}</div>`;
}

function renderTaskRows(tasks, emptyMessage = "当前没有待处理任务。") {
  if (!tasks.length) return `<div class="investigation-empty"><strong>${escapeHtml(emptyMessage)}</strong><p>这里不会用演示状态填充。</p></div>`;
  return `<div class="topic-task-list">${tasks.map((task) => {
    const stage = canonicalTaskStage(task);
    const intakeId = taskIntakeId(task);
    const taskId = task.id || task.task_id;
    const canReview = stage === "ready" && intakeId;
    const canRetryTask = (stage === "failed" || task.retryable === true) && taskId && !String(taskId).startsWith("intake:") && !String(taskId).startsWith("unassigned:");
    const intake = state.intakeItems.find((item) => item.id === intakeId);
    return `
      <article class="topic-task-row">
        <div>
          <h3>${escapeHtml(taskTitle(task))}</h3>
          <p>${escapeHtml(taskError(task) || (stage === "ready" ? "AI 候选已就绪，尚未写入正式档案。" : stage === "generating" ? "原始材料已保存，候选仍在生成。" : stage === "fetching" ? "正在抓取原始页面；搜索摘要不会进入证据链。" : stage === "queued" ? "任务已持久排队，尚未开始抓取。" : "任务状态来自服务端。"))}</p>
          <div class="topic-task-meta">${taskStatusMarkup(stage)}<span>${formatDate(task.updated_at || task.created_at || task.queued_at, true)}</span>${intakeId ? `<span>intake ${escapeHtml(intakeId)}</span>` : ""}</div>
        </div>
        <div class="topic-task-actions">
          ${canReview ? `<button class="btn btn-primary" type="button" data-investigation-action="open-review" data-intake-id="${escapeHtml(intakeId)}">打开审核</button><button class="btn btn-ghost" type="button" data-investigation-action="accept-entry" data-intake-id="${escapeHtml(intakeId)}">接受 / 编辑</button><button class="btn btn-ghost warning" type="button" data-investigation-action="reject-entry" data-intake-id="${escapeHtml(intakeId)}">驳回</button>` : ""}
          ${canRetryTask ? `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-task" data-task-id="${escapeHtml(taskId)}">重试任务</button>` : ""}
          ${stage === "failed" && intake?.status === "generation_failed" ? `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-intake" data-intake-id="${escapeHtml(intakeId)}">重新生成候选</button>` : ""}
          ${stage === "failed" && intake?.search?.result_id ? `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-search" data-search-result-id="${escapeHtml(intake.search.result_id)}" data-intake-id="${escapeHtml(intakeId)}">重试抓取</button>` : ""}
        </div>
      </article>`;
  }).join("")}</div>`;
}

function renderMiniMap(investigation) {
  const events = eventsForInvestigation(investigation).filter((event) => mapPosition(event));
  return `<div class="workbench-surface"><div class="workbench-surface-head"><div><h3>相关地点</h3><p>地图是已确认事件的辅助视图</p></div></div><div class="investigation-map">${events.map((event) => {
    const position = mapPosition(event);
    return `<button class="investigation-map-marker ${escapeHtml(event.importance || "medium")}" type="button" style="left:${position.left}%;top:${position.top}%" data-investigation-event="${escapeHtml(event.id)}" aria-label="${escapeHtml(event.title)}"></button>`;
  }).join("")}</div><div class="investigation-map-note">${events.length ? `${events.length} 个事件有可用坐标；点击节点打开正式事件档案。` : "当前没有可靠坐标，不会根据材料文本猜测位置。"}</div></div>`;
}

function renderInvestigationToday(investigation) {
  const tasks = tasksForInvestigation(investigation);
  const active = tasks.filter(taskIsActive);
  const metrics = investigationMetrics(investigation);
  const failed = active.filter((task) => canonicalTaskStage(task) === "failed").length;
  const processing = active.filter((task) => ["queued", "fetching", "generating"].includes(canonicalTaskStage(task))).length;
  return `${investigationPanelHeading("TODAY · ATTENTION QUEUE", "今天需要处理什么", "按处理阶段组织，不是资讯流；所有数字来自当前后端或明确标注的本地关联。", `<button class="btn btn-ghost" type="button" data-investigation-action="refresh">↻ 刷新</button>`)}
    <div class="investigation-stats">
      <div class="investigation-stat"><span>待人工审核</span><strong>${metrics.ready}</strong><small>ready</small></div>
      <div class="investigation-stat"><span>处理中</span><strong>${processing}</strong><small>queued / fetching / generating</small></div>
      <div class="investigation-stat"><span>失败待恢复</span><strong>${failed}</strong><small>failed</small></div>
      <div class="investigation-stat"><span>已确认事件</span><strong>${metrics.events}</strong><small>正式档案，不含候选</small></div>
    </div>
    <div class="investigation-today-grid">
      <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>优先处理</h3><p>失败与待审核优先，随后显示实际处理进度</p></div><span class="count-badge warning">${active.length}</span></div>${renderTaskRows(active)}</section>
      <aside class="investigation-rail">
        <div class="workbench-surface"><div class="workbench-surface-head"><div><h3>采集覆盖</h3><p>当前专题的固定网页</p></div></div><div class="workbench-surface-body"><p class="muted">${targetsForInvestigation(investigation).length} 个已关联监测来源。${state.collectionSummary ? "全局监测服务已连接。" : "监测摘要暂不可用。"}</p><button class="btn btn-ghost" type="button" data-investigation-action="monitoring">查看来源状态</button></div></div>
        ${renderMiniMap(investigation)}
      </aside>
    </div>`;
}

function renderInvestigationDiscovery(investigation) {
  const tasks = tasksForInvestigation(investigation).filter((task) => {
    const intake = state.intakeItems.find((item) => item.id === taskIntakeId(task));
    return task.subject_type === "search_result" || task.task_type === "search_result_intake" || intake?.input_type === "search";
  });
  return `${investigationPanelHeading("DISCOVERY", "发现资料", "外部搜索只产生候选线索；选中后才抓取原页并进入专题处理队列。", `<button class="btn btn-primary" type="button" data-investigation-action="search">⌕ 发起关键词发现</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>最近发现与处理</h3><p>搜索标题、摘要和排名不是 Evidence</p></div><span class="count-badge">${tasks.length}</span></div>${renderTaskRows(tasks, "该专题还没有关键词发现任务。")}</section>`;
}

function renderInvestigationMonitoring(investigation) {
  const targets = targetsForInvestigation(investigation);
  return `${investigationPanelHeading("RELIABLE COLLECTION", "监测来源", "固定公共网页的队列、运行、不可变版本与失败恢复；配置本身不是 Source/Evidence。", `<button class="btn btn-primary" type="button" data-investigation-action="add-source">＋ 添加固定网页</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>专题受管来源</h3><p>点击来源进入完整运行与版本视图</p></div><span class="count-badge">${targets.length}</span></div>
      ${targets.length ? `<div class="topic-task-list">${targets.map((target) => {
        const status = collectionTargetStatus(target);
        return `<article class="topic-task-row"><div><h3>${escapeHtml(target.name || "未命名来源")}</h3><p>${escapeHtml(target.url || target.canonical_url || "地址未知")}</p><div class="topic-task-meta"><span class="task-stage ${escapeHtml(status === "healthy" ? "ready" : status === "pending" ? "queued" : status)}">${escapeHtml(LABELS.collectionStatus[status] || status)}</span><span>V${escapeHtml(target.version_count ?? 0)}</span><span>${escapeHtml(collectionIntervalMinutes(target) ?? "?")} 分钟</span></div></div><div class="topic-task-actions"><button class="btn btn-ghost" type="button" data-investigation-action="open-source" data-target-id="${escapeHtml(target.id)}">打开运行记录</button></div></article>`;
      }).join("")}</div>` : '<div class="investigation-empty"><strong>该专题还没有关联固定来源</strong><p>添加一个无需登录的公共 HTTP(S) 网页，运行与变化会如实显示。</p></div>'}
    </section>`;
}

function renderInvestigationReview(investigation) {
  const tasks = tasksForInvestigation(investigation).filter(taskIsActive);
  return `${investigationPanelHeading("HUMAN REVIEW BOUNDARY", "待我审核", "ready 才可打开候选；接受、编辑和驳回都进入既有人工审核流程，不会一键伪造成功。", `<button class="btn btn-ghost" type="button" data-investigation-action="open-intake">打开完整采集箱</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>处理队列</h3><p>逐条显示 queued / fetching / generating / ready / failed</p></div><span class="count-badge warning">${tasks.length}</span></div>${renderTaskRows(tasks)}</section>`;
}

function renderInvestigationEvents(investigation) {
  const events = eventsForInvestigation(investigation);
  return `${investigationPanelHeading("CONFIRMED TIMELINE", "事件脉络", "这里只列出已确认事件；地图降为有坐标事件的辅助入口。", `<button class="btn btn-ghost" type="button" data-investigation-action="classic">打开经典事件视图</button>`)}
    <div class="investigation-events-grid">
      <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>已确认事件</h3><p>按开始时间排列</p></div><span class="count-badge">${events.length}</span></div>
        ${events.length ? `<div class="investigation-record-list">${[...events].sort((a, b) => new Date(a.start_at || 0) - new Date(b.start_at || 0)).map((event) => `<article class="investigation-record"><time>${formatDate(event.start_at)}</time><div><h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(event.summary || "暂无摘要")}</p></div><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">打开档案</button></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>该专题还没有已确认事件</strong><p>AI 候选不会在人工确认前出现在这里。</p></div>'}
      </section>
      ${renderMiniMap(investigation)}
    </div>`;
}

async function loadInvestigationEvidence(investigation) {
  const events = eventsForInvestigation(investigation);
  const missing = events.filter((event) => !state.investigationEventDetails.has(event.id));
  if (!missing.length) {
    if (state.activeInvestigationTab === "claims") renderInvestigationPage();
    return;
  }
  $("#investigation-panel").innerHTML = '<div class="investigation-empty"><strong>正在读取已确认主张与固定证据快照…</strong></div>';
  const results = await Promise.allSettled(missing.slice(0, 20).map((event) => api(`/pldr-api/v1/events/${encodeURIComponent(event.id)}`)));
  results.forEach((result) => { if (result.status === "fulfilled") state.investigationEventDetails.set(result.value.id, result.value); });
  if (state.activeInvestigationId === investigation.id && state.activeInvestigationTab === "claims") renderInvestigationPage();
}

function renderInvestigationClaims(investigation) {
  const events = eventsForInvestigation(investigation);
  const details = events.map((event) => state.investigationEventDetails.get(event.id)).filter(Boolean);
  if (events.length && details.length < events.length) {
    return '<div class="investigation-empty"><strong>正在读取已确认主张与证据…</strong><p>只读取正式事件接口，不会把候选补成证据。</p></div>';
  }
  const claims = details.flatMap((event) => (event.claims || []).map((claim) => ({ event, claim })));
  return `${investigationPanelHeading("CLAIMS & EVIDENCE", "主张与证据", "Evidence 固定回链到保存的快照；来源链接与搜索摘要不能替代快照。")}
    <div class="claims-stack">${claims.length ? claims.map(({ event, claim }) => `
      <article class="investigation-claim ${escapeHtml(claim.status || "unverified")}">
        <span class="task-stage ${claim.status === "confirmed" ? "ready" : claim.status === "contested" ? "generating" : "completed"}">${escapeHtml(LABELS.claim[claim.status] || claim.status || "待核实")}</span>
        <h3>${escapeHtml(claim.text)}</h3>
        ${(claim.evidence || []).map((evidence) => `<blockquote>${escapeHtml(evidence.snippet)}<footer><span>${escapeHtml(evidence.document?.source?.name || "来源未知")} · ${formatDate(evidence.document?.published_at)}</span><a href="${escapeHtml(withEventContext(evidence.snapshot_url || evidence.document?.snapshot_url, event.id))}" target="_blank" rel="noopener">打开固定快照 ↗</a></footer></blockquote>`).join("") || '<p class="muted">该主张尚未连接原文证据。</p>'}
        <footer><span>事件：${escapeHtml(event.title)}</span><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">打开事件档案</button></footer>
      </article>`).join("") : '<div class="investigation-empty"><strong>该专题还没有已确认主张</strong><p>候选不会提前出现在这里。</p></div>'}</div>`;
}

function reportsForInvestigation(investigation) {
  const detail = state.investigationDetails.get(investigation.id) || {};
  const server = unwrapItems(detail, "reports", "report_history");
  const local = loadLocalInvestigationState().reports[investigation.id] || [];
  const session = state.reportHistory.get(investigation.id) || [];
  return [...session, ...server, ...local].filter((report, index, list) => {
    const key = report.url || report.id;
    return list.findIndex((item) => (item.url || item.id) === key) === index;
  });
}

function renderInvestigationReports(investigation) {
  const reports = reportsForInvestigation(investigation);
  const canGenerate = eventsForInvestigation(investigation).length > 0;
  return `${investigationPanelHeading("REPORT ARTIFACTS", "专题报告", "只根据专题已确认对象生成；生成成功后才显示真实报告链接。", `<button class="btn btn-primary" type="button" data-investigation-action="generate-report" ${canGenerate ? "" : "disabled"}>▣ 生成专题报告</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>已生成报告</h3><p>${investigation.sync_mode === "local" ? "本页索引仅保存在此浏览器，报告文件仍来自服务端" : "服务端返回的报告制品"}</p></div><span class="count-badge">${reports.length}</span></div>
      ${reports.length ? reports.map((report) => `<article class="report-card"><div><h3>${escapeHtml(report.title || `PLDR 专题报告：${investigation.title}`)}</h3><p>${formatDate(report.created_at || report.generated_at, true)} · ${escapeHtml(report.evidence_count ?? "未知")} 条证据</p></div>${report.url ? `<a class="btn btn-ghost" href="${escapeHtml(report.url)}" target="_blank" rel="noopener">打开报告 ↗</a>` : '<span class="muted">未返回可打开链接</span>'}</article>`).join("") : `<div class="investigation-empty"><strong>尚未生成专题报告</strong><p>${canGenerate ? "不会用模板报告冒充生成结果。" : "请先人工确认至少一个事件；候选不能用于生成正式专题报告。"}</p></div>`}
    </section>`;
}

function activityActionLabel(entry) {
  const action = String(entry?.action || entry?.type || "记录");
  if (ACTIVITY_ACTION_LABELS[action]) return ACTIVITY_ACTION_LABELS[action];
  if (action.startsWith("task.")) {
    const stage = canonicalTaskStage({ status: action.slice(5) });
    return `任务状态变为：${String(LABELS.taskStage[stage] || stage).replace(/^[a-z_]+\s*·\s*/i, "")}`;
  }
  if (action.startsWith("intake_")) {
    const status = action.slice(7);
    return `采集箱状态：${LABELS.intakeStatus[status] || status}`;
  }
  if (action.startsWith("intake.")) {
    const status = action.slice(7);
    return `材料状态变为：${LABELS.intakeStatus[status] || status}`;
  }
  return action;
}

function activityStatusValue(value) {
  const raw = String(value ?? "");
  const stage = canonicalTaskStage({ status: raw });
  return LABELS.investigationStatus[raw]
    || LABELS.intakeStatus[raw]
    || String(LABELS.taskStage[stage] || raw).replace(/^[a-z_]+\s*·\s*/i, "");
}

function activityValue(value, key = "") {
  if (value === null || value === undefined || value === "") return "";
  if (["status", "from_status", "to_status", "intake_status", "task_status", "outcome"].includes(key)) return activityStatusValue(value);
  if (key === "scope") return LABELS.searchScope[value] || String(value);
  if (key === "role") return ({ member: "专题成员", primary: "主要资料", context: "背景资料" })[value] || String(value);
  if (key === "classification") return ({ legacy: "既有资料", "demo-document": "内置示例资料", "legacy-confirmed": "既有已确认资料" })[value] || String(value);
  if (key === "latency_ms") return `${value} ms`;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    const primitives = value.filter((item) => ["string", "number"].includes(typeof item));
    if (primitives.length === value.length && value.length <= 3) return primitives.join("、");
    return `${value.length} 项`;
  }
  if (typeof value === "object") return `${Object.keys(value).length} 项明细`;
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length > 140 ? `${text.slice(0, 137)}…` : text;
}

function activityDetailSummary(entry) {
  const detail = entry?.detail ?? entry?.detail_json;
  if (typeof detail === "string") return activityValue(detail);
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    if (entry.action === "investigation.updated" && detail.before && detail.after) {
      const fieldLabels = { title: "专题名称", question: "核心问题", description: "说明", status: "状态" };
      const changes = Object.keys(fieldLabels).filter((key) => String(detail.before[key] ?? "") !== String(detail.after[key] ?? ""));
      if (changes.length) return changes.map((key) => `${fieldLabels[key]}：${activityValue(detail.before[key], key) || "未填写"} → ${activityValue(detail.after[key], key) || "未填写"}`).join("；");
    }
    const priorityKeys = [
      "title", "question", "keyword", "scope", "result_count", "latency_ms", "outcome",
      "reason", "error", "error_message", "error_class", "attempt_number", "from_status",
      "to_status", "intake_status", "task_status", "event_count", "evidence_count", "filename",
      "classification", "role", "retry_model",
    ];
    const entries = [
      ...priorityKeys.filter((key) => Object.prototype.hasOwnProperty.call(detail, key)).map((key) => [key, detail[key]]),
      ...Object.entries(detail).filter(([key]) => !priorityKeys.includes(key) && !["before", "after", "url", "event_ids", "batch_id", "intake_item_id"].includes(key)),
    ];
    const parts = entries.map(([key, value]) => {
      const shown = activityValue(value, key);
      return shown ? `${ACTIVITY_DETAIL_LABELS[key] || key}：${shown}` : "";
    }).filter(Boolean).slice(0, 4);
    if (parts.length) return parts.join("；");
  }
  const objectTypeLabels = { investigation: "专题", search_query: "检索", search_result: "搜索结果", intake: "材料", collection_target: "监测来源", event: "事件", report: "报告" };
  if (entry?.object_id) return `${objectTypeLabels[entry.object_type] || entry.object_type || "对象"}：${entry.object_id}`;
  return "服务端记录了这次操作，未提供更多说明。";
}

function activityActorLabel(entry) {
  if (entry.local_only) return "本地记录";
  if (entry.derived) return "来自既有记录";
  const actor = String(entry.actor || "服务端");
  if (actor === "analyst") return "分析员";
  if (actor === "system:migration") return "系统迁移";
  if (actor === "system:collector") return "采集服务";
  if (actor.startsWith("collector:")) return "采集服务";
  return actor;
}

function renderInvestigationActivity(investigation) {
  const server = state.investigationActivities.get(investigation.id) || [];
  const local = loadLocalInvestigationState().activities[investigation.id] || [];
  const derived = investigation.sync_mode === "compatibility" ? state.intakeItems.map((item) => ({
    id: `derived:${item.id}`,
    action: `intake_${item.status}`,
    detail: `${intakeTitle(item)} · ${LABELS.intakeStatus[item.status] || item.status}`,
    actor: "采集箱记录",
    created_at: item.reviewed_at || item.updated_at || item.created_at,
    derived: true,
  })) : [];
  const activities = [...server, ...local, ...derived].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  return `${investigationPanelHeading("AUDIT TRAIL", "操作记录", "服务端活动与浏览器本地动作明确区分；这不是对缺失审计数据的推测。")}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>专题活动</h3><p>按时间倒序</p></div><span class="count-badge">${activities.length}</span></div>
      ${activities.length ? `<div class="investigation-record-list">${activities.map((entry) => `<article class="investigation-record"><time>${formatDate(entry.created_at, true)}</time><div><h3>${escapeHtml(activityActionLabel(entry))}</h3><p>${escapeHtml(entry.message || entry.summary || activityDetailSummary(entry))}</p></div><span class="sync-badge ${entry.local_only || entry.derived ? "local" : "server"}">${escapeHtml(activityActorLabel(entry))}</span></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>暂无操作记录</strong><p>没有记录时保持为空，不会补写示例活动。</p></div>'}
    </section>`;
}

function openInvestigationCreateModal() {
  const modal = $("#investigation-create-modal");
  $("#investigation-create-form").reset();
  $("#investigation-create-result").textContent = state.investigationMode === "unavailable" ? "专题服务不可用；提交后将创建明确标注的浏览器本地草稿。" : "";
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  $("#investigation-create-name").focus();
}

function closeInvestigationCreateModal() {
  const modal = $("#investigation-create-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
}

async function submitInvestigationCreate(event) {
  event.preventDefault();
  const button = $("#investigation-create-submit");
  button.disabled = true;
  button.textContent = "正在创建…";
  $("#investigation-create-result").className = "import-result";
  try {
    const investigation = await createInvestigation({
      title: $("#investigation-create-name").value,
      question: $("#investigation-create-question").value,
      description: $("#investigation-create-description").value,
    });
    closeInvestigationCreateModal();
    renderInvestigationHome();
    toast(investigation.sync_mode === "local" ? "已创建浏览器本地专题草稿；尚未同步服务端。" : "专题已在服务端持久创建。", investigation.sync_mode === "local" ? "info" : "success", 6500);
    await openInvestigation(investigation.id, "today");
  } catch (error) {
    $("#investigation-create-result").className = "import-result error";
    $("#investigation-create-result").textContent = `创建失败：${error.message}。未显示虚假成功。`;
  } finally {
    button.disabled = false;
    button.textContent = "创建并进入专题";
  }
}

async function retryInvestigationTask(taskId) {
  const investigation = activeInvestigation();
  if (!investigation || !taskId) return;
  try {
    await api(API_ROUTES.taskRetry(taskId), { method: "POST", body: JSON.stringify({ actor: "analyst" }) });
    toast("重试已由服务端接受，任务回到队列。", "success");
    await loadInvestigationWorkspace(investigation.id, { quiet: true });
  } catch (error) {
    toast(`任务重试失败：${error.message}`, "error", 7000);
  }
}

async function generateInvestigationReport() {
  const investigation = activeInvestigation();
  if (!investigation) return;
  setBusy(true, "正在生成专题报告");
  try {
    let result;
    if (isServerInvestigation(investigation)) {
      result = await api(API_ROUTES.reports, { method: "POST", body: JSON.stringify({ investigation_id: investigation.id, title: `PLDR 专题报告：${investigation.title}` }) });
    } else {
      const ids = eventsForInvestigation(investigation).map((event) => event.id);
      if (!ids.length) throw new Error("该专题没有已确认事件，无法生成证据报告");
      result = await api(API_ROUTES.reports, { method: "POST", body: JSON.stringify({ event_ids: ids, title: `PLDR 专题报告：${investigation.title}` }) });
    }
    const report = { ...result, title: result.title || `PLDR 专题报告：${investigation.title}`, created_at: result.created_at || new Date().toISOString() };
    state.reportHistory.set(investigation.id, [report, ...(state.reportHistory.get(investigation.id) || [])]);
    if (investigation.sync_mode === "local") {
      const local = loadLocalInvestigationState();
      local.reports[investigation.id] = [report, ...(local.reports[investigation.id] || [])].slice(0, 50);
      saveLocalInvestigationState();
    }
    toast(`专题报告已生成，共 ${result.evidence_count ?? "未知"} 条证据。`, "success");
    if (isServerInvestigation(investigation)) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    else renderInvestigationPage();
    if (result.url) window.open(result.url, "_blank", "noopener");
  } catch (error) {
    toast(`专题报告生成失败：${error.message}`, "error", 7000);
  } finally {
    setBusy(false);
  }
}

async function handleInvestigationAction(action, node) {
  const investigation = activeInvestigation();
  if (action === "create") return openInvestigationCreateModal();
  if (action === "search") return openExternalSearchModal(investigation?.id);
  if (action === "import") return openImportModal(investigation?.id);
  if (action === "review") return setInvestigationTab("review");
  if (action === "monitoring") return setInvestigationTab("monitoring");
  if (action === "classic") return showClassicWorkspace();
  if (action === "open-intake") return openIntakeModal();
  if (action === "refresh") {
    await refreshData({ keepSelection: true, quiet: true });
    await refreshInvestigationDirectory();
    if (investigation) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    toast("专题数据已刷新。", "success");
    return;
  }
  if (action === "add-source") {
    state.pendingCollectionInvestigationId = investigation?.id || null;
    return openCollectionModal();
  }
  if (action === "open-source") {
    state.pendingCollectionInvestigationId = null;
    const modal = $("#collection-modal");
    if (typeof modal.showModal === "function") modal.showModal(); else modal.setAttribute("open", "");
    return refreshCollectionData(node.dataset.targetId);
  }
  if (["open-review", "accept-entry", "reject-entry"].includes(action)) {
    const intakeId = node.dataset.intakeId;
    await openIntakeModal(intakeId);
    if (action === "accept-entry") toast("请核对或编辑候选，预览影响后再确认入档。", "info", 6000);
    if (action === "reject-entry") toast("请在审核页填写驳回原因后执行驳回。", "info", 6000);
    return;
  }
  if (action === "retry-task") return retryInvestigationTask(node.dataset.taskId);
  if (action === "retry-intake") {
    state.selectedIntakeId = node.dataset.intakeId;
    await handleIntakeAction("regenerate");
    if (investigation) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    return;
  }
  if (action === "retry-search") {
    await retryExternalSearchResult(node.dataset.searchResultId);
    if (investigation) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    return;
  }
  if (action === "generate-report") return generateInvestigationReport();
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
  const overviewMetrics = state.overview?.metrics || {};
  const intake = state.overview?.intake || {};
  const collection = state.collectionSummary?.metrics || state.collectionSummary || {};
  const changed = collection.changed_pending ?? collection.pending_changes ?? collection.pending_review ?? collection.changed ?? 0;
  const classicVisible = $("#classic-workspace-shell") && !$("#classic-workspace-shell").hidden;
  const investigation = !classicVisible ? activeInvestigation() : null;
  let items;
  if (classicVisible) {
    items = [
      ["events", overviewMetrics.events ?? 0, "全局事件"],
      ["documents", overviewMetrics.documents ?? 0, "全局文档"],
      ["independence", overviewMetrics.independence_groups ?? 0, "独立源组"],
      ["contested", overviewMetrics.contested_claims ?? 0, "争议主张"],
      ["intake", intake.candidate_ready ?? 0, "全局待审"],
      ["collection", changed, "监测待审"],
    ];
    $("#metrics").setAttribute("aria-label", "经典事件视图全局指标");
  } else if (investigation) {
    const metrics = investigationMetrics(investigation);
    items = [
      ["queue", metrics.tasks, "专题待处理"],
      ["review", metrics.ready, "专题待审核"],
      ["events", metrics.events, "专题事件"],
      ["sources", metrics.sources, "专题来源"],
    ];
    $("#metrics").setAttribute("aria-label", `${investigation.title} 专题指标`);
  } else {
    const userInvestigations = state.investigations.filter((item) => !["system", "demo", "compatibility"].includes(item.sync_mode));
    const assignments = allHomeAssignments();
    items = [
      ["investigations", userInvestigations.length, "我的专题"],
      ["queue", assignments.length, "待我处理"],
      ["review", assignments.filter(({ task }) => canonicalTaskStage(task) === "ready").length, "待我审核"],
      ["failed", assignments.filter(({ task }) => canonicalTaskStage(task) === "failed").length, "失败待恢复"],
    ];
    $("#metrics").setAttribute("aria-label", "专题首页指标");
  }
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
  const rawLatitude = event.location?.latitude;
  const rawLongitude = event.location?.longitude;
  if (rawLatitude === null || rawLatitude === undefined || rawLongitude === null || rawLongitude === undefined) return null;
  if ((typeof rawLatitude === "string" && !rawLatitude.trim()) || (typeof rawLongitude === "string" && !rawLongitude.trim())) return null;
  const latitude = Number(rawLatitude);
  const longitude = Number(rawLongitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return null;
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
    const result = await api(API_ROUTES.reports, {
      method: "POST",
      body: JSON.stringify({
        event_ids: ids,
        title: event ? `PLDR 证据简报：${event.title}` : null,
      }),
    });
    toast(`简报已生成，共 ${result.evidence_count} 条证据。`, "success");
    window.open(result.url, "_blank", "noopener");
    return result;
  } catch (error) {
    toast(`简报生成失败：${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

function openImportModal(preferredInvestigationId = state.activeInvestigationId) {
  const modal = $("#import-modal");
  $("#import-result").textContent = "";
  $("#import-form").reset();
  setImportMode("url");
  renderDestinationPickers(preferredInvestigationId);
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

function openExternalSearchModal(preferredInvestigationId = state.activeInvestigationId) {
  const modal = $("#search-modal");
  renderDestinationPickers(preferredInvestigationId);
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

function searchResultLinkedToDestination(result) {
  const destinationId = $("#search-destination")?.value;
  if (!destinationId || destinationId === NEW_INVESTIGATION_VALUE) return false;
  const investigation = destinationId === UNASSIGNED_VALUE
    ? unclassifiedInvestigation()
    : state.investigations.find((item) => item.id === destinationId);
  if (!investigation && destinationId === UNASSIGNED_VALUE) return Boolean(result.selection);
  if (!investigation) return false;
  const intakeId = result.selection?.intake_item_id || result.selection?.intake?.id;
  if (intakeId && linkedIds(investigation, "intake").includes(intakeId)) return true;
  return tasksForInvestigation(investigation).some((task) => {
    const resultId = task.subject?.id || task.subject_id || task.payload?.result_id || task.payload_json?.result_id;
    return resultId === result.id;
  });
}

function searchSelectionLabel(result, linked = searchResultLinkedToDestination(result)) {
  const selection = result.selection;
  if (linked) return "已在当前专题";
  if (!selection) return "选择";
  const status = LABELS.intakeStatus[selection.intake_status || selection.status] || selection.status;
  if (selection.intake_status === "failed") return `${status} · 可关联后重试`;
  return `采集箱已有 · ${status || "可复用"}`;
}

function renderSearchResults() {
  const root = $("#search-results");
  if (!root) return;
  const checkedIds = new Set($$("input[type='checkbox']:checked", root).map((input) => input.value));
  root.innerHTML = state.searchResults.length ? state.searchResults.map((result) => {
    const linked = searchResultLinkedToDestination(result);
    return `
    <article class="search-result ${linked ? "selected" : result.selection ? "existing" : ""}" role="listitem">
      <label class="search-select">
        <input type="checkbox" value="${escapeHtml(result.id)}" ${linked ? "disabled" : checkedIds.has(result.id) ? "checked" : ""}>
        <span>${escapeHtml(searchSelectionLabel(result, linked))}</span>
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
  `; }).join("") : '<div class="search-empty">没有匹配结果。PLDR 不会用演示数据填充空结果。</div>';
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
    const destinationId = $("#search-destination").value;
    const destination = destinationId === UNASSIGNED_VALUE
      ? unclassifiedInvestigation()
      : state.investigations.find((item) => item.id === destinationId);
    const context = ["server", "system"].includes(destination?.sync_mode) ? { investigation_id: destination.id } : {};
    const payload = await api(API_ROUTES.search, {
      method: "POST",
      body: JSON.stringify({
        keyword,
        scope: $("#search-scope").value,
        language: $("#search-language").value,
        limit: 10,
        ...context,
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
  let intent;
  try {
    intent = destinationIntent("search");
  } catch (error) {
    $("#search-status").className = "search-status error";
    $("#search-status").textContent = error.message;
    return;
  }
  state.searchBusy = true;
  const button = $("#search-select");
  button.disabled = true;
  button.textContent = "提交处理中";
  $("#search-status").className = "search-status";
  $("#search-status").textContent = "正在提交逐条处理任务；搜索摘要不会进入证据链。";
  let requestAccepted = false;
  try {
    const useServerContext = state.investigationMode === "server" || state.investigationMode === "error";
    const context = ["existing", "unassigned"].includes(intent.type) && ["server", "system"].includes(intent.investigation?.sync_mode)
      ? { investigation_id: intent.investigation.id }
      : intent.type === "new" && useServerContext
        ? { new_investigation: { ...intent.fields, status: "active" } }
        : {};
    const payload = await api(API_ROUTES.searchSelect, {
      method: "POST",
      body: JSON.stringify({ result_ids: selectedIds, request_id: makeClientId("search-request"), actor: "analyst", ...context }),
    });
    requestAccepted = true;
    let investigation = intent.investigation || null;
    if (intent.type === "new") {
      if (payload.investigation) {
        investigation = normalizeInvestigation(payload.investigation, "server");
        state.investigationMode = "server";
        state.investigations = [investigation, ...state.investigations.filter((item) => item.id !== investigation.id && item.id !== "__legacy_overview__")];
      } else if (!useServerContext) {
        investigation = createLocalInvestigation(intent.fields);
      }
    }

    const responseEntries = payload.tasks || payload.results || [];
    const asynchronous = payload.status === "queued" || payload.batch || responseEntries.some((entry) => entry.task_id || ["queued", "fetching", "generating"].includes(entry.state || entry.intake_status));
    const taskRows = responseEntries.map((entry) => ({
      ...entry,
      id: entry.id || entry.task_id,
      status: entry.state || entry.status || entry.intake_status || "queued",
      subject_id: entry.result_id || entry.subject_id,
      subject_type: entry.subject_type || "search_result",
      task_type: entry.task_type || "search_result_intake",
      created_at: entry.created_at || new Date().toISOString(),
    }));
    if (investigation && taskRows.length) {
      state.investigationTasks.set(investigation.id, [...taskRows, ...(state.investigationTasks.get(investigation.id) || [])]);
    }

    const intakeIds = responseEntries.map((entry) => entry.intake_item_id).filter(Boolean);
    let association = { linked: 0, failed: 0, mode: "server" };
    if (investigation && investigation.sync_mode === "local" && intakeIds.length) {
      association = await associateInvestigationObjects(investigation, "intake", intakeIds, { origin: "external_search" });
    } else if (isServerInvestigation(investigation) && !asynchronous && intakeIds.length) {
      association = await associateInvestigationObjects(investigation, "intake", intakeIds, { origin: "external_search_legacy" });
    }

    closeExternalSearchModal();
    renderInvestigationHome();
    if (investigation) {
      await openInvestigation(investigation.id, "review");
    } else {
      showInvestigationHome();
    }
    if (asynchronous) {
      toast(`已提交 ${responseEntries.length || selectedIds.length} 条任务；正在专题中逐条显示真实进度。`, "success", 6500);
    } else {
      const updates = new Map((payload.results || []).map((entry) => [entry.result_id, entry.result]));
      state.searchResults = state.searchResults.map((result) => updates.get(result.id) || result);
      const failures = (payload.results || []).filter((entry) => entry.intake_status === "failed").length;
      toast(failures ? `已处理 ${responseEntries.length} 项，其中 ${failures} 项抓取失败。` : `已处理 ${responseEntries.length} 项并进入人工候选流程。`, failures ? "error" : "success", 6500);
    }
    if (association.failed) toast(`材料已进入采集箱，但 ${association.failed} 条专题关联失败；没有声称关联成功。`, "error", 7500);
    await refreshIntakeData().catch(() => null);
    renderInvestigationHome();
    if (!investigation && intakeIds[0]) await openIntakeModal(intakeIds[0]);
    if (isServerInvestigation(investigation)) await loadInvestigationWorkspace(investigation.id, { quiet: true });
  } catch (error) {
    if (requestAccepted) {
      closeExternalSearchModal();
      showInvestigationHome();
      toast(`处理请求已被服务端接受，但专题关联或页面刷新失败：${error.message}。请在待处理队列核对。`, "error", 9000);
    } else {
      $("#search-status").className = "search-status error";
      $("#search-status").textContent = `提交处理任务失败：${error.message}。未显示排队或成功状态。`;
    }
  } finally {
    state.searchBusy = false;
    button.textContent = "加入专题并开始处理";
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
  let intent;
  try {
    intent = destinationIntent("import");
  } catch (error) {
    $("#import-result").className = "import-result error";
    $("#import-result").textContent = error.message;
    return;
  }
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
  $("#import-result").textContent = "正在保存材料；候选生成状态会在专题队列中单独显示…";

  let persistedCount = 0;
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
    if (!count) throw new Error("服务端没有返回采集箱条目，无法确认导入结果");
    persistedCount = count;
    let investigation = intent.investigation || null;
    let association = { linked: 0, failed: 0, mode: "unassigned", errors: [] };
    try {
      if (intent.type === "new") investigation = await createInvestigation(intent.fields);
      if (investigation) {
        association = await associateInvestigationObjects(investigation, "intake", items.map((item) => item.id), { origin: `manual_${mode}` });
      }
    } catch (associationError) {
      association = { linked: 0, failed: count, mode: "failed", errors: [associationError.message] };
    }

    await refreshData({ keepSelection: true, quiet: true });
    await refreshInvestigationDirectory();
    closeImportModal();
    if (investigation && association.failed === 0) {
      toast(investigation.sync_mode === "local"
        ? `已导入 ${count} 条真实材料；专题归类仅保存在此浏览器。`
        : `已导入并关联到专题：${count} 条；候选仍需人工审核。`, "success", 7000);
      await openInvestigation(investigation.id, "review");
    } else {
      toast(association.failed
        ? `材料已真实进入采集箱，但专题关联失败：${association.errors?.[0] || "未知错误"}`
        : `材料已进入待归类采集箱：${count} 条。`, association.failed ? "error" : "success", 8000);
      showInvestigationHome();
      await openIntakeModal(items[0].id);
    }
  } catch (error) {
    $("#import-result").className = `import-result ${persistedCount ? "success" : "error"}`;
    $("#import-result").textContent = persistedCount
      ? `服务端已保存 ${persistedCount} 条材料，但后续归类或页面刷新失败：${error.message}。请到采集箱核对。`
      : `导入失败：${error.message}。未显示虚假成功。`;
    if (persistedCount) toast(`材料已保存，但后续归类或页面刷新失败：${error.message}`, "error", 9000);
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
  const activeCount = state.intakeItems.filter((item) => ["queued", "parsed", "candidate_ready", "generation_failed", "failed"].includes(item.status)).length;
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
    queued: "材料占位与处理任务已持久排队，尚未开始抓取；未进入正式档案。",
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
        ${item.search_history?.length ? `<div><dt>处理追踪</dt><dd><ul class="search-trace-list">${item.search_history.map((trace) => `<li><strong>${escapeHtml(SEARCH_TRACE_OUTCOME_LABELS[trace.outcome] || trace.outcome || "状态未知")}</strong> · ${escapeHtml(trace.keyword || "未知关键词")} · ${escapeHtml(trace.channel || trace.provider || "未知渠道")} · ${formatDate(trace.selected_at, true)} · ${escapeHtml(trace.result_id || "未知结果")}</li>`).join("")}</ul></dd></div>` : ""}
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

async function continueInvestigationReview(completedIntakeId) {
  const investigation = activeInvestigation();
  if (!investigation) return;
  if (isServerInvestigation(investigation)) await loadInvestigationWorkspace(investigation.id, { quiet: true });
  else renderInvestigationPage();
  const next = tasksForInvestigation(investigation).find((task) => canonicalTaskStage(task) === "ready" && taskIntakeId(task) && taskIntakeId(task) !== completedIntakeId);
  if (next) {
    await refreshIntakeData(taskIntakeId(next));
    toast("已打开专题中的下一条 ready 材料。", "success", 4200);
    return;
  }
  closeIntakeModal();
  setInvestigationTab("review", { syncUrl: false });
  toast("本专题当前没有下一条 ready 材料，已返回处理队列。", "info", 5200);
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
      if (isServerInvestigation(activeInvestigation())) await loadInvestigationWorkspace(state.activeInvestigationId, { quiet: true });
      else if (activeInvestigation()) renderInvestigationPage();
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
      if (isServerInvestigation(activeInvestigation())) await loadInvestigationWorkspace(state.activeInvestigationId, { quiet: true });
      else if (activeInvestigation()) renderInvestigationPage();
    } catch (error) {
      toast(`原始页重试失败：${error.message}`, "error", 7000);
    }
    return;
  }
  if (item.status !== "candidate_ready") return;
  let dispositionCommitted = false;
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
      dispositionCommitted = true;
      toast(`已原子入档：${result.result.formal_object_ids.event}`, "success");
      await refreshData({ keepSelection: false, quiet: true, preferredEventId: result.result.formal_object_ids.event });
      await refreshIntakeData(item.id);
      renderIntakeDetail(selectedIntakeItem());
      await continueInvestigationReview(item.id);
      return;
    }
    if (action === "reject") {
      const reason = $("#intake-reject-reason")?.value.trim();
      if (!reason) throw new Error("请填写驳回原因。");
      await api(`/pldr-api/v1/intake/${item.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason }),
      });
      dispositionCommitted = true;
      toast("候选已驳回，未写入正式区。", "success");
    } else if (action === "cancel") {
      await api(`/pldr-api/v1/intake/${item.id}/cancel`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason: "Analyst cancelled before confirmation" }),
      });
      dispositionCommitted = true;
      toast("处理已撤销，未写入正式区。", "success");
    }
    await refreshData({ keepSelection: true, quiet: true });
    await refreshIntakeData(item.id);
    await continueInvestigationReview(item.id);
  } catch (error) {
    toast(dispositionCommitted ? `人工处置已由服务端完成，但页面刷新失败：${error.message}` : `采集箱操作失败：${error.message}`, "error", 7000);
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
  state.pendingCollectionInvestigationId = null;
}

async function submitCollectionTarget(event) {
  event.preventDefault();
  if (state.collectionBusy) return;
  state.collectionBusy = true;
  const button = $("#collection-add");
  button.disabled = true;
  button.textContent = "正在保存并加入队列…";
  const destination = state.investigations.find((item) => item.id === state.pendingCollectionInvestigationId) || null;
  try {
    const result = await api("/pldr-api/v1/collection/targets", {
      method: "POST",
      body: JSON.stringify({
        name: $("#collection-name").value.trim(),
        url: $("#collection-url").value.trim(),
        interval_seconds: Number($("#collection-interval").value) * 60,
        language: $("#collection-language").value,
        run_immediately: $("#collection-run-immediately").checked,
        ...(isServerInvestigation(destination) ? { investigation_id: destination.id, actor: "analyst" } : {}),
      }),
    });
    const run = result.run || result.queued_run || null;
    const runFailed = run?.status === "failed";
    toast(runFailed ? `来源已保存，但首次抓取失败：${collectionRunError(run) || "未知错误"}` : run?.status === "queued" ? "固定来源已保存，首次试抓已进入持久队列。" : "固定来源已保存。变化只会进入待审箱。", runFailed ? "error" : "success", 7000);
    if (destination && result.target?.id) {
      try {
        const association = await associateInvestigationObjects(destination, "collection_target", [result.target.id], { origin: "fixed_url_monitor" });
        if (association.failed) {
          toast("来源已保存，但专题关联失败；没有显示为已归入专题。", "error", 7500);
        } else {
          toast(destination.sync_mode === "local" ? "来源已在本浏览器关联到本地专题草稿。" : "来源已关联到专题。", destination.sync_mode === "local" ? "info" : "success", 6000);
        }
      } catch (associationError) {
        toast(`来源已保存，但专题关联失败：${associationError.message}`, "error", 7500);
      }
    }
    state.pendingCollectionInvestigationId = null;
    $("#collection-source-form").reset();
    $("#collection-run-immediately").checked = true;
    state.collectionBusy = false;
    await refreshCollectionData(result.target?.id);
    try {
      await refreshData({ keepSelection: true, quiet: true });
      await refreshInvestigationDirectory();
      if (destination && state.activeInvestigationId === destination.id) await loadInvestigationWorkspace(destination.id, { quiet: true });
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
    if (state.investigations.length) {
      renderInvestigationHome();
      if (activeInvestigation()) renderInvestigationPage();
    }
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
  $("#btn-workbench-home").addEventListener("click", () => showInvestigationHome());
  $("#btn-classic-workspace").addEventListener("click", () => showClassicWorkspace());
  $("#btn-create-investigation").addEventListener("click", openInvestigationCreateModal);
  $("#btn-home-search").addEventListener("click", () => openExternalSearchModal());
  $("#btn-home-import").addEventListener("click", () => openImportModal());
  $("#btn-investigation-back").addEventListener("click", () => showInvestigationHome());
  $("#investigation-create-close").addEventListener("click", closeInvestigationCreateModal);
  $("#investigation-create-cancel").addEventListener("click", closeInvestigationCreateModal);
  $("#investigation-create-form").addEventListener("submit", submitInvestigationCreate);
  $("#search-destination").addEventListener("change", () => updateDestinationFields("search"));
  $("#import-destination").addEventListener("change", () => updateDestinationFields("import"));
  $("#search").addEventListener("input", applyFilters);
  $("#importance-filter").addEventListener("change", applyFilters);
  $("#language-filter").addEventListener("change", applyFilters);
  $("#contested-filter").addEventListener("change", applyFilters);
  $("#btn-refresh").addEventListener("click", async () => {
    await refreshData();
    await refreshInvestigationDirectory();
    if (activeInvestigation()) await loadInvestigationWorkspace(state.activeInvestigationId, { quiet: true });
  });
  $("#btn-report").addEventListener("click", () => generateReport());
  $("#btn-collection").addEventListener("click", () => {
    state.pendingCollectionInvestigationId = null;
    openCollectionModal();
  });
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
    const investigationTab = event.target.closest("[data-investigation-tab]");
    if (investigationTab) {
      setInvestigationTab(investigationTab.dataset.investigationTab);
      return;
    }
    const investigationAction = event.target.closest("[data-investigation-action]");
    if (investigationAction) {
      handleInvestigationAction(investigationAction.dataset.investigationAction, investigationAction);
      return;
    }
    const investigationEvent = event.target.closest("[data-investigation-event]");
    if (investigationEvent) {
      selectEvent(investigationEvent.dataset.investigationEvent, { open: true, syncUrl: false });
      return;
    }
    const assignment = event.target.closest("[data-investigation-assignment]");
    if (assignment) {
      const investigationId = assignment.dataset.investigationId;
      if (investigationId) openInvestigation(investigationId, "review");
      else if (assignment.dataset.intakeId) openIntakeModal(assignment.dataset.intakeId);
      return;
    }
    const investigationCard = event.target.closest("[data-investigation-id]");
    if (investigationCard && investigationCard.classList.contains("investigation-card")) {
      openInvestigation(investigationCard.dataset.investigationId, "today");
      return;
    }
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
      if ($("#classic-workspace-shell").hidden) openExternalSearchModal();
      else $("#search").focus();
    }
    if (event.key === "Escape") {
      if ($("#event-drawer").classList.contains("open")) closeDrawer();
      else if ($("#collection-modal").open) closeCollectionModal();
      else if ($("#search-modal").open) closeExternalSearchModal();
      else if ($("#intake-modal").open) closeIntakeModal();
      else if ($("#import-modal").open) closeImportModal();
      else if ($("#investigation-create-modal").open) closeInvestigationCreateModal();
    }
    const card = event.target.closest?.(".event-card");
    if (card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectEvent(card.dataset.eventId, { open: true });
    }
  });

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(window.location.search);
    const investigationId = params.get("investigation");
    const tab = params.get("tab") || "today";
    if (params.get("view") === "classic" || params.get("event")) showClassicWorkspace({ syncUrl: false });
    else if (investigationId) openInvestigation(investigationId, tab, { syncUrl: false });
    else showInvestigationHome({ syncUrl: false });
  });
}

async function init() {
  bindEvents();
  try {
    const routeParams = new URLSearchParams(window.location.search);
    const requestedEvent = routeParams.get("event");
    await refreshData({
      keepSelection: false,
      quiet: true,
      preferredEventId: requestedEvent,
      syncSelectionUrl: false,
    });
    await refreshInvestigationDirectory();
    if (requestedEvent && state.selectedId === requestedEvent) {
      showClassicWorkspace({ syncUrl: false });
      openDrawer();
    } else if (requestedEvent) {
      const url = new URL(window.location.href);
      url.searchParams.delete("event");
      history.replaceState(null, "", url);
      showInvestigationHome({ syncUrl: false });
    } else if (routeParams.get("view") === "classic") {
      showClassicWorkspace({ syncUrl: false });
    } else if (routeParams.get("investigation")) {
      await openInvestigation(routeParams.get("investigation"), routeParams.get("tab") || "today", { syncUrl: false });
    } else {
      showInvestigationHome({ syncUrl: false });
    }
  } catch {
    setRouteVisibility("investigation");
    $("#investigation-app").innerHTML = `
      <section class="fatal-state">
        <span>!</span>
        <h1>PLDR 数据连接失败</h1>
        <p>请确认后端服务已经启动。页面没有用示例数据伪装连接成功。</p>
        <button class="btn btn-primary" type="button" onclick="location.reload()">重新连接</button>
      </section>`;
  }
}

init();
