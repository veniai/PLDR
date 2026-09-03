const state = {
  overview: null,
  events: [],
  filteredEvents: [],
  selectedId: null,
  selectedEvent: null,
  selectedEventError: null,
  selectedEventRequestSerial: 0,
  eventOverviewScopeInvestigationId: null,
  eventOverviewScopeRequestSerial: 0,
  sources: [],
  config: null,
  drawerTab: "overview",
  importMode: "url",
  intakeItems: [],
  globalIntakeItems: [],
  intakeOptions: { events: [], entities: [] },
  selectedIntakeId: null,
  intakeScopeInvestigationId: null,
  intakeScopeInvestigationTitle: null,
  intakeMobileStep: 0,
  intakeRequestSerial: 0,
  intakePreviewRequestSerial: 0,
  intakePreviewApproval: null,
  intakeActionBusy: false,
  intakeActionSerial: 0,
  intakeDrafts: {},
  selectedIntakeIds: new Set(),
  intakeVisibility: "active",
  searchRun: null,
  searchResults: [],
  searchCurrentPageIds: [],
  searchSelectedIds: new Set(),
  searchSelectionsByRun: new Map(),
  searchHistory: [],
  searchHistoryInvestigationId: null,
  searchHistoryBusy: false,
  searchHistoryError: "",
  searchHistoryRequestSerial: 0,
  searchHistoryAvailable: true,
  searchHistoryVisibility: "active",
  searchPage: 1,
  searchPageSize: 20,
  searchHasMore: false,
  searchNextPage: null,
  searchNextCursor: null,
  searchLastRequest: null,
  searchError: "",
  searchErrorInfo: null,
  searchErrorSource: null,
  searchBusy: false,
  searchRequestSerial: 0,
  collectionSummary: null,
  collectionTargets: [],
  selectedCollectionTargetId: null,
  selectedCollectionTarget: null,
  collectionDiff: null,
  collectionBusy: false,
  collectionRequestSerial: 0,
  collectionDiffRequestSerial: 0,
  collectionReviewSerial: 0,
  collectionRefreshSerial: 0,
  collectionPollTimer: null,
  investigations: [],
  investigationMode: "loading",
  investigationError: "",
  activeInvestigationId: null,
  activeInvestigationTab: "outcomes",
  investigationSectionByTab: { materials: "discovery", outcomes: "events" },
  investigationDetails: new Map(),
  investigationTasks: new Map(),
  investigationTaskErrors: new Map(),
  investigationActivities: new Map(),
  investigationActivityErrors: new Map(),
  investigationLinks: new Map(),
  investigationEventDetails: new Map(),
  investigationEventErrors: new Map(),
  investigationEventRequestTokens: new Map(),
  investigationOutcomes: new Map(),
  investigationOutcomeErrors: new Map(),
  investigationPollTimer: null,
  investigationRequestSerial: 0,
  investigationCreateRequestSerial: 0,
  importRequestSerial: 0,
  collectionSubmitSerial: 0,
  localInvestigationState: null,
  pendingCollectionInvestigationId: null,
  collectionScopeInvestigationId: null,
  collectionScopeInvestigationTitle: null,
  collectionScopeTargetIds: null,
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
  investigationOutcome: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/outcome`,
  investigationReorganization: (id, action) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/reorganization/${action}`,
  investigationLinks: (id) => `/pldr-api/v1/investigations/${encodeURIComponent(id)}/links`,
  investigationIntakeAction: (investigationId, itemId, action) => `/pldr-api/v1/investigations/${encodeURIComponent(investigationId)}/intake/${encodeURIComponent(itemId)}/${action}`,
  taskRetry: (id) => `/pldr-api/v1/tasks/${encodeURIComponent(id)}/retry`,
  intakeAction: (id, action) => `/pldr-api/v1/intake/${encodeURIComponent(id)}/${action}`,
  search: "/pldr-api/v1/search",
  searchRuns: "/pldr-api/v1/search/runs",
  searchRun: (id) => `/pldr-api/v1/search/runs/${encodeURIComponent(id)}`,
  searchRunAction: (id, action) => `/pldr-api/v1/search/runs/${encodeURIComponent(id)}/${action}`,
  searchSelect: "/pldr-api/v1/search/select",
  reports: "/pldr-api/v1/reports",
});

const LOCAL_INVESTIGATION_KEY = "pldr.investigations.v1";
const NEW_INVESTIGATION_VALUE = "__new_investigation__";
const UNASSIGNED_VALUE = "__unassigned__";
const INVESTIGATION_TABS = new Set(["overview", "materials", "outcomes", "activity"]);
const INVESTIGATION_SECTIONS = Object.freeze({
  materials: new Set(["discovery", "monitoring"]),
  outcomes: new Set(["events", "claims", "reports"]),
});
const LEGACY_INVESTIGATION_TABS = Object.freeze({
  today: { tab: "overview" },
  review: { tab: "overview" },
  discovery: { tab: "materials", section: "discovery" },
  monitoring: { tab: "materials", section: "monitoring" },
  events: { tab: "outcomes", section: "events" },
  claims: { tab: "outcomes", section: "claims" },
  reports: { tab: "outcomes", section: "reports" },
});
const INVESTIGATION_TASK_PAGE_SIZE = 500;
const INVESTIGATION_DIRECTORY_PAGE_SIZE = 500;
const INVESTIGATION_ACTIVITY_PAGE_SIZE = 500;
const HOME_ASSIGNMENT_WINDOW = 100;
const GLOBAL_INTAKE_LOAD_LIMIT = 500;
const ACTIVE_INTAKE_STATUSES = new Set(["queued", "parsed", "candidate_ready", "generation_failed", "failed"]);

const LABELS = {
  importance: { critical: "极高", high: "高", medium: "中", low: "低" },
  claim: {
    confirmed: "人工确认",
    supported: "多源印证",
    single_source: "单一来源",
    contested: "存在冲突",
    unverified: "缺少依据",
    refuted: "已有反证",
  },
  stance: { supports: "支持", contradicts: "冲突", context: "背景" },
  source: { healthy: "正常", stale: "陈旧", error: "异常", disabled: "停用" },
  mode: { "curated-demo": "人工整理演示", live: "实时专题", cached: "缓存专题" },
  intakeStatus: {
    queued: "等待处理",
    parsed: "已解析",
    candidate_ready: "等待确认",
    generation_failed: "生成失败",
    confirmed: "已确认入档",
    rejected: "不采用",
    cancelled: "已撤销",
    failed: "采集失败",
    archived: "已删除",
  },
  inputType: { web: "公共网页", text: "粘贴文本", file: "本地文件", rss: "RSS", search: "外部搜索结果", collection: "固定网页版本", rss_collection: "RSS 监测条目" },
  collectionTargetType: { web_page: "固定网页", rss_feed: "RSS / Atom" },
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
    items: "条目检查",
    failed: "抓取失败",
  },
  discoveredItemStatus: { pending: "待处理", ready: "可审核", failed: "失败" },
  investigationStatus: { active: "进行中", paused: "已暂停", closed: "已关闭", archived: "已归档" },
  taskStage: {
    queued: "处理中",
    waiting: "处理中",
    fetching: "处理中",
    running: "处理中",
    generating: "处理中",
    parsed: "处理中",
    ready: "待核对",
    candidate_ready: "待核对",
    failed: "需要处理",
    blocked: "需要处理",
    retrying: "处理中",
    completed: "已完成",
    confirmed: "已完成",
    accepted: "已完成",
    rejected: "已完成",
    cancelled: "已完成",
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
  "search.query_archived": "把查询记录移到已删除",
  "search.query_restored": "恢复了查询记录",
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
  "task.rejected": "人工决定不采用，任务已完成",
  "task.deduplicated": "复用了已有处理任务",
  "task.reused": "复用了已有处理任务",
  "task.lease_recovered": "超时任务已恢复到队列",
  "task.retry": "重新提交了失败任务",
  "task.retry_scheduled": "AI 未完成，系统已自动重新排队",
  "intake.confirmed": "人工确认材料入档",
  "intake.rejected": "人工决定不采用材料",
  "intake.cancelled": "撤销材料处理",
  "intake.archived": "把材料移到全局已删除",
  "intake.restored": "恢复了全局材料",
  "intake.removed_from_investigation": "把材料从本专题移除",
  "intake.restored_to_investigation": "把材料恢复到本专题",
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

function unitIntervalValue(value, fallback) {
  if (value === null || value === undefined || String(value).trim() === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? clamp(number, 0, 1) : fallback;
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

function formatEventDate(value) {
  const dateOnly = String(value || "").match(
    /^(\d{4})-(\d{2})-(\d{2})T00:00:00(?:\.000)?Z$/i,
  );
  return dateOnly
    ? `${dateOnly[1]}/${dateOnly[2]}/${dateOnly[3]}`
    : formatDate(value, true);
}

function eventTimeInputValue(value) {
  const raw = String(value || "");
  const dateOnly = raw.match(/^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.000)?Z$/i);
  return dateOnly ? dateOnly[1] : raw;
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
    error.traceId = response.headers.get("x-request-id") || response.headers.get("trace-id") || null;
    throw error;
  }
  return payload;
}

const ERROR_STAGE_LABELS = Object.freeze({
  query: "公开资料检索",
  search: "公开资料检索",
  fetch: "抓取原始页面",
  parse: "提取正文",
  extract: "提取正文",
  generate: "生成 AI 候选",
  model: "生成 AI 候选",
  link: "关联专题",
  confirm: "确认入档",
  unknown: "处理资料",
});

function objectMessage(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  return value.message || value.summary || value.title || value.detail || value.reason || "";
}

function legacyErrorBlueprint(message, fallbackStage = "unknown") {
  const text = String(message || "未知错误");
  const lower = text.toLowerCase();
  if (/model|glm|candidate/.test(lower) && /timeout|deadline|timed out|90 second/.test(lower)) {
    return {
      stage: "generate",
      title: "AI 候选生成超时",
      message: "原始材料已经保存，但模型没有在时限内返回可审核候选。",
      impact: "材料不会丢失，也还没有进入正式事件档案。",
      nextAction: "稍后只重试候选生成，不需要重新搜索或重新抓取原文。",
      retryable: true,
    };
  }
  if (/non-public address|private address|ssrf|blocked.*address/.test(lower)) {
    return {
      stage: "fetch",
      title: "安全检查阻止了页面抓取",
      message: "系统解析到非公网地址，因此没有请求这个页面。",
      impact: "搜索线索仍保留，但没有原始页面快照，不能作为证据入档。",
      nextAction: "请先让管理员检查 DNS 或代理解析，或换用公开正文页；当前配置不变时重复重试不会解决问题，也不要关闭安全防护。",
      retryable: false,
    };
  }
  if (/\b401\b|\b403\b|forbidden|unauthorized|login|paywall/.test(lower)) {
    return {
      stage: "fetch",
      title: "来源网站拒绝访问",
      message: "原站要求登录、授权或拒绝了当前抓取方式。",
      impact: "没有获得可固定的原始页面快照，正式档案没有变化。",
      nextAction: "先打开原网页核对；如材料允许公开使用，可改为粘贴正文或上传文件。",
      retryable: false,
    };
  }
  if (/\b429\b|rate.?limit|too many requests/.test(lower)) {
    return {
      stage: fallbackStage === "unknown" ? "query" : fallbackStage,
      title: "外部服务暂时限流",
      message: "请求频率超过了外部服务当前允许的范围。",
      impact: "本次操作没有产生新的正式档案。",
      nextAction: "等待一会儿再重试，不要连续重复提交。",
      retryable: true,
    };
  }
  if (/not configured|missing.*key|api key|credential/.test(lower)) {
    return {
      stage: fallbackStage === "unknown" ? "query" : fallbackStage,
      title: "外部服务尚未配置",
      message: "当前环境缺少可用的检索或模型配置。",
      impact: "没有生成虚假结果，正式档案没有变化。",
      nextAction: "请管理员检查服务配置后再试。",
      retryable: false,
    };
  }
  if (/timeout|deadline|timed out/.test(lower)) {
    return {
      stage: fallbackStage,
      title: `${ERROR_STAGE_LABELS[fallbackStage] || ERROR_STAGE_LABELS.unknown}超时`,
      message: "外部服务没有在时限内完成本次操作。",
      impact: "尚未完成的步骤不会被显示为成功；已保存的材料仍然保留。",
      nextAction: "稍后重试一次；若持续发生，请展开技术详情并提供诊断编号。",
      retryable: true,
    };
  }
  if (/network|connection|dns|resolve|fetch failed/.test(lower)) {
    return {
      stage: fallbackStage,
      title: "暂时无法连接外部服务",
      message: "网络、DNS 或上游服务连接失败。",
      impact: "本次未完成的步骤没有写入正式档案。",
      nextAction: "确认网络恢复后重试；持续失败时把诊断编号交给管理员。",
      retryable: true,
    };
  }
  return {
    stage: fallbackStage,
    title: `${ERROR_STAGE_LABELS[fallbackStage] || ERROR_STAGE_LABELS.unknown}没有完成`,
    message: "系统保留了失败状态，没有把未完成的操作显示为成功。",
    impact: "正式档案不会因本次失败而发生未经确认的改变。",
    nextAction: "可重试一次；若仍失败，请展开技术详情并提供诊断编号。",
    retryable: true,
  };
}

function normalizeOperationalError(input, fallbackStage = "unknown") {
  const payload = input?.payload || input || {};
  const rawDetail = payload?.detail && typeof payload.detail === "object" ? payload.detail : payload;
  const structured = rawDetail?.error && typeof rawDetail.error === "object" ? { ...rawDetail, ...rawDetail.error } : rawDetail;
  const rawMessage = objectMessage(structured)
    || objectMessage(payload?.detail)
    || input?.error_message
    || input?.last_error
    || input?.candidate_generation?.error
    || input?.message
    || (typeof input === "string" ? input : "")
    || "未知错误";
  const blueprint = legacyErrorBlueprint(rawMessage, structured?.stage || input?.stage || fallbackStage);
  const technical = structured?.technical_detail
    || structured?.technical_message
    || structured?.debug_detail
    || rawMessage;
  return {
    code: structured?.code || input?.error_code || "PLDR_OPERATION_FAILED",
    stage: structured?.stage || input?.error_stage || blueprint.stage || fallbackStage,
    title: structured?.title || structured?.summary || blueprint.title,
    message: structured?.display_message || structured?.why || structured?.message || blueprint.message,
    impact: structured?.impact || blueprint.impact,
    nextAction: structured?.next_action || structured?.recommended_action || blueprint.nextAction,
    retryable: structured?.retryable ?? input?.retryable ?? blueprint.retryable,
    traceId: structured?.trace_id || structured?.request_id || input?.trace_id || input?.traceId || null,
    technical: typeof technical === "string" ? technical : JSON.stringify(technical, null, 2),
  };
}

function renderOperationalError(input, { stage = "unknown", compact = false, actionHtml = "" } = {}) {
  const error = normalizeOperationalError(input, stage);
  const trace = error.traceId ? `
    <span class="error-trace">诊断编号 <code>${escapeHtml(error.traceId)}</code><button class="text-btn" type="button" data-copy-trace="${escapeHtml(error.traceId)}">复制</button></span>` : "";
  const detailRows = `<dl>
      <div><dt>发生了什么</dt><dd>${escapeHtml(error.message)}</dd></div>
      <div><dt>影响</dt><dd>${escapeHtml(error.impact)}</dd></div>
      <div><dt>怎么办</dt><dd>${escapeHtml(error.nextAction)}</dd></div>
    </dl>`;
  const technical = `<div class="error-technical"><strong>诊断信息${error.code ? ` · ${escapeHtml(error.code)}` : ""}</strong><pre>${escapeHtml(error.technical)}</pre>${trace}</div>`;
  return compact ? `
    <article class="operational-error compact" role="alert">
      <div class="operational-error-heading">
        <span class="error-stage">${escapeHtml(ERROR_STAGE_LABELS[error.stage] || ERROR_STAGE_LABELS.unknown)}</span>
        <strong>${escapeHtml(error.title)}</strong>
      </div>
      <p class="operational-error-summary">${escapeHtml(error.message)}</p>
      ${actionHtml ? `<div class="operational-error-action">${actionHtml}</div>` : ""}
      <details><summary>查看原因、影响与诊断</summary>${detailRows}${technical}</details>
    </article>` : `
    <article class="operational-error" role="alert">
      <div class="operational-error-heading">
        <span class="error-stage">${escapeHtml(ERROR_STAGE_LABELS[error.stage] || ERROR_STAGE_LABELS.unknown)}</span>
        <strong>${escapeHtml(error.title)}</strong>
      </div>
      ${detailRows}
      ${actionHtml ? `<div class="operational-error-action">${actionHtml}</div>` : ""}
      <details><summary>技术详情${error.code ? ` · ${escapeHtml(error.code)}` : ""}</summary><pre>${escapeHtml(error.technical)}</pre>${trace}</details>
    </article>`;
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

function recordIsArchived(record) {
  return Boolean(record?.archived || record?.archived_at || record?.removed || record?.removed_at
    || record?.link?.archived || record?.link?.archived_at || record?.membership?.archived || record?.membership?.archived_at);
}

async function loadAllInvestigationTasks(investigationId, visibility = "active") {
  const tasksById = new Map();
  let offset = 0;
  let total = null;
  do {
    const params = new URLSearchParams({ offset: String(offset), limit: String(INVESTIGATION_TASK_PAGE_SIZE), visibility });
    const payload = await api(`${API_ROUTES.investigationTasks(investigationId)}?${params}`);
    const page = unwrapItems(payload, "items", "tasks", "review_tasks");
    page.forEach((task) => tasksById.set(task.id, task));
    const reportedTotal = Number(payload?.count);
    if (Number.isFinite(reportedTotal) && reportedTotal >= 0) total = Math.max(total ?? 0, reportedTotal);
    offset += page.length;
    if (!page.length || (total === null && page.length < INVESTIGATION_TASK_PAGE_SIZE)) break;
  } while (total === null || offset < total);
  return [...tasksById.values()];
}

async function loadAllInvestigations() {
  const byId = new Map();
  let offset = 0;
  let total = null;
  do {
    const params = new URLSearchParams({ offset: String(offset), limit: String(INVESTIGATION_DIRECTORY_PAGE_SIZE) });
    const payload = await api(`${API_ROUTES.investigations}?${params}`);
    const page = unwrapItems(payload, "items", "investigations");
    page.forEach((investigation) => byId.set(investigation.id, investigation));
    const reportedTotal = Number(payload?.count);
    if (Number.isFinite(reportedTotal) && reportedTotal >= 0) total = reportedTotal;
    offset += page.length;
    if (!page.length || (total === null && page.length < INVESTIGATION_DIRECTORY_PAGE_SIZE)) break;
  } while (total === null || offset < total);
  return [...byId.values()];
}

async function loadAllInvestigationActivity(investigationId) {
  const byId = new Map();
  let offset = 0;
  let total = null;
  do {
    const params = new URLSearchParams({ offset: String(offset), limit: String(INVESTIGATION_ACTIVITY_PAGE_SIZE) });
    const payload = await api(`${API_ROUTES.investigationActivity(investigationId)}?${params}`);
    const page = unwrapItems(payload, "items", "activity", "activities", "decisions");
    page.forEach((entry) => byId.set(entry.id, entry));
    const reportedTotal = Number(payload?.count);
    if (Number.isFinite(reportedTotal) && reportedTotal >= 0) total = reportedTotal;
    offset += page.length;
    if (!page.length || (total === null && page.length < INVESTIGATION_ACTIVITY_PAGE_SIZE)) break;
  } while (total === null || offset < total);
  return [...byId.values()];
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
    tracking_mode: source.tracking_mode || "one_time",
    event_start_at: source.event_start_at || null,
    event_end_at: source.event_end_at || null,
    settings: {
      source_language: "auto",
      report_language: "zh-CN",
      publication_window: "30d",
      auto_select_limit: 5,
      ...(source.settings || source.settings_json || {}),
    },
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
    tracking_mode: fields.tracking_mode || "one_time",
    event_start_at: fields.event_start_at || null,
    event_end_at: fields.event_end_at || null,
    settings: fields.settings || {},
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
    tracking_mode: fields.tracking_mode || "one_time",
    event_start_at: fields.event_start_at || null,
    event_end_at: fields.tracking_mode === "continuous" ? null : fields.event_end_at || null,
    settings: fields.settings || {},
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
  return task?.intake_item_id || task?.intake_item?.id || task?.intake?.id || task?.result?.intake_item_id || null;
}

function taskTitle(task) {
  const intakeId = taskIntakeId(task);
  const intake = task?.intake_item || state.intakeItems.find((item) => item.id === intakeId);
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
  return task?.error_message || (typeof error === "object" ? error?.message || error?.summary || error?.title || error?.class : error) || task?.last_error || "";
}

function taskErrorPayload(task) {
  if (!task) return null;
  if (task.error && typeof task.error === "object") return { ...task.error, retryable: task.retryable ?? task.error.retryable };
  const message = taskError(task);
  return message ? { message, stage: task.error_stage || canonicalTaskStage(task), retryable: task.retryable } : null;
}

function taskStatusMarkup(stage, task = null) {
  const safe = canonicalTaskStage({ status: stage });
  const humanState = task?.waiting_for_model_retry ? "等待 AI 重试" : LABELS.taskStage[safe]
    || (["ready"].includes(safe) ? "待核对" : ["failed", "blocked"].includes(safe) ? "需要处理" : ["accepted", "rejected", "cancelled", "completed"].includes(safe) ? "已完成" : "处理中");
  return `<span class="task-stage ${escapeHtml(safe)}">${escapeHtml(humanState)}</span>`;
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

function taskBelongsInPending(task) {
  if (!["search_result", "search_result_intake"].includes(task?.subject_type)
      && task?.task_type !== "search_result_intake") return true;
  if (task?.selection_origin !== "topic_onboarding") return true;
  return !["uncertain", "unlikely"].includes(task?.topic_relevance?.level);
}

function investigationMetrics(investigation) {
  const raw = investigation?.metrics || investigation?.counts || investigation?.raw?.metrics || investigation?.raw?.counts || {};
  const tasks = tasksForInvestigation(investigation);
  const pendingTasks = tasks.filter((task) => taskIsActive(task) && taskBelongsInPending(task));
  const taskStatus = investigation?.task_status || investigation?.raw?.task_status || {};
  const statusActive = ["queued", "fetching", "generating", "ready", "failed"].reduce((sum, key) => sum + Number(taskStatus[key] || 0), 0);
  const processing = ["queued", "fetching", "generating"].reduce((sum, key) => sum + Number(taskStatus[key] || 0), 0);
  const hasLoadedTasks = tasks.length > 0;
  const ready = hasLoadedTasks
    ? pendingTasks.filter((task) => canonicalTaskStage(task) === "ready").length
    : Number(raw.ready ?? raw.review_ready ?? taskStatus.ready ?? 0);
  const failed = hasLoadedTasks
    ? pendingTasks.filter((task) => canonicalTaskStage(task) === "failed").length
    : Number(raw.failed ?? taskStatus.failed ?? 0);
  return {
    tasks: hasLoadedTasks ? pendingTasks.length : Number(raw.pending_tasks ?? statusActive),
    ready,
    failed,
    processing: hasLoadedTasks
      ? pendingTasks.filter((task) => ["queued", "fetching", "generating"].includes(canonicalTaskStage(task))).length
      : processing,
    attention: ready + failed,
    events: Number(raw.events ?? raw.event_count ?? eventsForInvestigation(investigation).length),
    sources: Number(raw.sources ?? raw.collection_targets ?? raw.source_count ?? targetsForInvestigation(investigation).length),
  };
}

function allHomeAssignments() {
  const assignments = [];
  const seenIntake = new Set();
  const hasServerBackedDirectory = state.investigations.some((investigation) => (
    ["server", "system", "demo"].includes(investigation.sync_mode)
  ));
  state.investigations.forEach((investigation) => {
    if (investigation.status === "archived") return;
    if (["demo", "system"].includes(investigation.sync_mode)) return;
    if (investigation.sync_mode === "compatibility" && state.investigations.length > 1) return;
    tasksForInvestigation(investigation).filter(taskIsActive).forEach((task) => {
      const intakeId = taskIntakeId(task);
      if (intakeId) seenIntake.add(intakeId);
      if (!taskBelongsInPending(task)) return;
      assignments.push({ task, investigation });
    });
  });
  // A server-backed directory is authoritative for the home queue. Falling
  // back to every globally active intake here would make material removed
  // from (or left behind by) an archived topic reappear as "unassigned".
  // Legacy/local-only deployments still need the global-intake fallback.
  if (!hasServerBackedDirectory) {
    state.intakeItems.filter((item) => ["queued", "parsed", "candidate_ready", "generation_failed", "failed"].includes(item.status) && !seenIntake.has(item.id)).forEach((item) => {
      assignments.push({
        task: { id: `unassigned:${item.id}`, intake_item_id: item.id, status: taskStageFromIntake(item), title: intakeTitle(item), error_message: item.error, created_at: item.created_at },
        investigation: null,
      });
    });
  }
  const rank = { failed: 0, ready: 1, fetching: 2, generating: 3, queued: 4 };
  return assignments.sort((a, b) => (rank[canonicalTaskStage(a.task)] ?? 8) - (rank[canonicalTaskStage(b.task)] ?? 8)
    || new Date(b.task.updated_at || b.task.created_at || 0) - new Date(a.task.updated_at || a.task.created_at || 0));
}

function renderDestinationPickers(preferredId = state.activeInvestigationId) {
  const candidates = state.investigations.filter((item) => !["compatibility", "system", "demo"].includes(item.sync_mode) && item.status !== "archived");
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
          : "材料处理状态会在专题的“待处理”中持续显示。";
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
    note.textContent = `专题服务读取失败：${state.investigationError}。没有伪造同步结果；可继续使用事件总览与既有采集箱。`;
  } else {
    note.className = "investigation-sync-note";
    note.textContent = "正在读取专题服务…";
  }

  const userInvestigations = state.investigations.filter((item) => !["system", "demo", "compatibility"].includes(item.sync_mode) && item.status !== "archived");
  const archivedInvestigations = state.investigations.filter((item) => !["system", "demo", "compatibility"].includes(item.sync_mode) && item.status === "archived");
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
          <span><strong>${metrics.attention}</strong>待处理</span>
          <span><strong>${metrics.processing}</strong>系统处理中</span>
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
    </div>`}${archivedInvestigations.length ? `<details class="investigation-archive"><summary>已删除专题（${archivedInvestigations.length}）</summary><div class="investigation-archive-list">${archivedInvestigations.map((investigation) => `<article class="investigation-archived-card"><div><strong>${escapeHtml(investigation.title)}</strong><small>${escapeHtml(investigation.question)}</small></div><button class="btn btn-ghost" type="button" data-investigation-action="restore-topic" data-investigation-target="${escapeHtml(investigation.id)}">恢复</button></article>`).join("")}</div></details>` : ""}${referenceInvestigations.length ? `<div class="investigation-list-divider"><span>参考入口</span><small>不计入“我的专题”，也不能作为资料归类目标</small></div>${renderCards(referenceInvestigations)}` : ""}`;

  const assignments = allHomeAssignments().filter(({ task }) => ["ready", "failed"].includes(canonicalTaskStage(task)));
  const visibleAssignments = assignments.slice(0, HOME_ASSIGNMENT_WINDOW);
  $("#assignment-count").textContent = String(assignments.length);
  $("#assignment-list").innerHTML = assignments.length ? `${visibleAssignments.map(({ task, investigation }) => {
    const stage = canonicalTaskStage(task);
    return `
      <button class="assignment-card" type="button" data-investigation-assignment="${escapeHtml(task.id)}" data-investigation-id="${escapeHtml(investigation?.id || "")}" data-intake-id="${escapeHtml(taskIntakeId(task) || "")}">
        <span class="assignment-priority ${escapeHtml(stage)}"></span>
        <span class="assignment-copy">
          <h3>${escapeHtml(taskTitle(task))}</h3>
          <span class="assignment-meta"><span>${escapeHtml(investigation?.sync_mode === "system" ? "系统待归类" : investigation?.title || "待归类材料")}</span><span>${formatDate(task.updated_at || task.created_at, true)}</span></span>
          ${task?.waiting_for_model_retry ? "<p>AI 分析未完成，后台将自动重试。</p>" : taskError(task) ? `<p>${escapeHtml(normalizeOperationalError(taskErrorPayload(task), canonicalTaskStage(task)).title)}</p>` : ""}
          <span class="assignment-meta">${taskStatusMarkup(stage, task)}</span>
        </span>
        <span class="assignment-go">›</span>
      </button>`;
  }).join("")}${assignments.length > visibleAssignments.length ? `<div class="investigation-empty"><strong>首页显示优先级最高的 ${visibleAssignments.length} / ${assignments.length} 条</strong><p>完整队列没有被计数截断；请进入对应专题查看其全部任务。</p></div>` : ""}` : '<div class="investigation-empty"><strong>当前没有待处理项</strong><p>这里不会用示例任务填充。新采集、网页变化或失败任务出现后会显示。</p></div>';
  renderDestinationPickers();
  renderMetrics();
}

async function refreshInvestigationDirectory() {
  const local = loadLocalInvestigationState();
  let serverInvestigations = [];
  try {
    const payload = await loadAllInvestigations();
    serverInvestigations = payload.map((item) => normalizeInvestigation(item, "server"));
    state.investigationMode = "server";
    state.investigationError = "";
    const taskScope = serverInvestigations.filter((investigation) => investigation.status !== "archived");
    const taskLists = await Promise.allSettled(taskScope.map((investigation) => loadAllInvestigationTasks(investigation.id)));
    taskLists.forEach((result, index) => {
      const investigationId = taskScope[index].id;
      if (result.status === "fulfilled") {
        state.investigationTasks.set(investigationId, result.value);
        state.investigationTaskErrors.delete(investigationId);
      } else {
        state.investigationTasks.delete(investigationId);
        state.investigationTaskErrors.set(investigationId, result.reason);
      }
    });
    const taskFailures = taskLists.filter((result) => result.status === "rejected");
    if (taskFailures.length) {
      state.investigationMode = "error";
      state.investigationError = `${taskFailures.length} 个专题的处理队列读取失败：${taskFailures[0].reason?.message || "未知错误"}`;
    }
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
  const unavailableScopeId = state.eventOverviewScopeInvestigationId
    && !state.investigations.some((item) => item.id === state.eventOverviewScopeInvestigationId)
    ? state.eventOverviewScopeInvestigationId
    : null;
  if (unavailableScopeId) {
    const url = new URL(window.location.href);
    ["investigation", "tab", "section", "view", "event", "event_scope"].forEach((key) => url.searchParams.delete(key));
    history.replaceState(null, "", url);
    showInvestigationHome({ syncUrl: false });
    toast("当前专题已不在可用目录中，已返回“我的专题”。没有改为显示全局事件。", "error", 7500);
    return;
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
    loadAllInvestigationTasks(investigation.id),
    loadAllInvestigationActivity(investigation.id),
    api(API_ROUTES.investigationOutcome(investigation.id)),
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
  if (calls[1].status === "fulfilled") {
    state.investigationTasks.set(investigation.id, calls[1].value);
    state.investigationTaskErrors.delete(investigation.id);
  } else {
    state.investigationTasks.delete(investigation.id);
    state.investigationTaskErrors.set(investigation.id, calls[1].reason);
  }
  if (calls[2].status === "fulfilled") {
    state.investigationActivities.set(investigation.id, calls[2].value);
    state.investigationActivityErrors.delete(investigation.id);
  } else {
    state.investigationActivities.delete(investigation.id);
    state.investigationActivityErrors.set(investigation.id, calls[2].reason);
  }
  if (calls[3].status === "fulfilled") {
    const outcome = calls[3].value;
    state.investigationOutcomes.set(investigation.id, outcome);
    state.investigationOutcomeErrors.delete(investigation.id);
    (outcome.events || []).forEach((event) => {
      state.investigationEventDetails.set(event.id, event);
      state.investigationEventErrors.delete(event.id);
    });
  } else {
    state.investigationOutcomes.delete(investigation.id);
    state.investigationOutcomeErrors.set(investigation.id, calls[3].reason);
  }
  const detailLinks = unwrapItems(state.investigationDetails.get(investigation.id), "links");
  state.investigationLinks.set(investigation.id, detailLinks);
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

function normalizeInvestigationRoute(tab = "outcomes", section = null) {
  const legacy = LEGACY_INVESTIGATION_TABS[tab];
  if (legacy) return { tab: legacy.tab, section: legacy.section || null };
  const normalizedTab = INVESTIGATION_TABS.has(tab) ? tab : "outcomes";
  const allowedSections = INVESTIGATION_SECTIONS[normalizedTab];
  const fallbackSection = normalizedTab === "materials" ? "discovery" : normalizedTab === "outcomes" ? "events" : null;
  return {
    tab: normalizedTab,
    section: allowedSections?.has(section) ? section : fallbackSection,
  };
}

function activeInvestigationSection(tab = state.activeInvestigationTab) {
  return state.investigationSectionByTab[tab] || null;
}

function investigationShowsClaims() {
  return state.activeInvestigationTab === "outcomes";
}

function investigationNeedsEvidence() {
  return investigationShowsClaims();
}

function eventOverviewInvestigation() {
  return state.investigations.find((item) => item.id === state.eventOverviewScopeInvestigationId) || null;
}

function eventOverviewScopeUnavailable() {
  return Boolean(state.eventOverviewScopeInvestigationId && !eventOverviewInvestigation());
}

function eventOverviewEvents() {
  const investigation = eventOverviewInvestigation();
  if (state.eventOverviewScopeInvestigationId && !investigation) return [];
  return investigation ? eventsForInvestigation(investigation) : state.events;
}

function renderEventOverviewScope() {
  const investigation = eventOverviewInvestigation();
  const scope = $("#event-overview-scope");
  const back = $("#event-overview-return");
  if (scope) {
    scope.innerHTML = eventOverviewScopeUnavailable()
      ? "<strong>专题范围不可用</strong> · 已停止显示，不会回退到全局事件"
      : investigation
      ? `<strong>当前专题</strong> · 只显示“${escapeHtml(investigation.title)}”已确认的事件`
      : "<strong>全局范围</strong> · 显示全部已确认事件";
  }
  if (back) {
    back.hidden = !investigation;
    back.textContent = investigation ? `← 返回“${investigation.title}”` : "← 返回专题";
  }
}

function syncInvestigationUrl(investigationId, tab, section, { replace = false } = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("investigation", investigationId);
  url.searchParams.set("tab", tab);
  if (section) url.searchParams.set("section", section); else url.searchParams.delete("section");
  url.searchParams.delete("view");
  url.searchParams.delete("event");
  url.searchParams.delete("event_scope");
  history[replace ? "replaceState" : "pushState"](null, "", url);
}

async function ensureEventOverviewScopeLoaded(investigation, { force = false } = {}) {
  if (!investigation || !isServerInvestigation(investigation) || (!force && state.investigationDetails.has(investigation.id))) return;
  const detail = await api(API_ROUTES.investigation(investigation.id));
  state.investigationDetails.set(investigation.id, detail);
  const updated = normalizeInvestigation(detail.investigation || detail, "server");
  state.investigations = state.investigations.map((item) => item.id === investigation.id
    ? { ...item, ...updated, raw: { ...item.raw, ...updated.raw } }
    : item);
  const detailLinks = unwrapItems(detail, "links");
  state.investigationLinks.set(investigation.id, detailLinks);
}

async function reconcileEventOverviewSelection() {
  const scopedEvents = state.filteredEvents;
  const url = new URL(window.location.href);
  const requestedEvent = url.searchParams.get("event");
  if (requestedEvent && !scopedEvents.some((event) => event.id === requestedEvent)) {
    url.searchParams.delete("event");
    history.replaceState(null, "", url);
    if ($("#event-drawer")?.classList.contains("open")) closeDrawer();
  }
  if (scopedEvents.some((event) => event.id === state.selectedId)) {
    if (state.selectedEvent?.id !== state.selectedId) {
      await selectEvent(state.selectedId, { open: false, syncUrl: false });
    } else {
      renderAssessment();
      renderGaps();
      $("#btn-report").disabled = false;
    }
    return;
  }
  state.selectedEventRequestSerial += 1;
  state.selectedId = null;
  state.selectedEvent = null;
  state.selectedEventError = null;
  $("#btn-report").disabled = true;
  renderAssessment();
  renderGaps();
  if ($("#event-drawer")?.classList.contains("open")) renderDrawer();
  if (scopedEvents[0]) {
    await selectEvent(scopedEvents[0].id, { open: false, syncUrl: false });
  }
}

function setRouteVisibility(route) {
  const classic = route === "classic";
  $("#investigation-app").hidden = classic;
  $("#classic-workspace-shell").hidden = !classic;
  $("#btn-workbench-home").classList.toggle("active", !classic);
  $("#btn-classic-workspace").classList.toggle("active", classic);
}

function closeRouteScopedSurfaces() {
  if ($("#intake-modal")?.open) closeIntakeModal();
  if ($("#collection-modal")?.open) closeCollectionModal();
  if ($("#search-modal")?.open) closeExternalSearchModal();
  if ($("#import-modal")?.open) closeImportModal();
  if ($("#investigation-create-modal")?.open) closeInvestigationCreateModal();
}

function showInvestigationHome({ syncUrl = true } = {}) {
  closeRouteScopedSurfaces();
  resetEventOverviewContext();
  state.activeInvestigationId = null;
  state.eventOverviewScopeInvestigationId = null;
  if ($("#event-drawer")?.classList.contains("open")) closeDrawer();
  if (state.investigationPollTimer) window.clearTimeout(state.investigationPollTimer);
  state.investigationPollTimer = null;
  setRouteVisibility("investigation");
  $("#investigation-home").hidden = false;
  $("#investigation-detail-page").hidden = true;
  renderInvestigationHome();
  if (syncUrl) {
    const url = new URL(window.location.href);
    ["investigation", "tab", "section", "view", "event", "event_scope"].forEach((key) => url.searchParams.delete(key));
    history.pushState(null, "", url);
  }
}

async function showClassicWorkspace({ syncUrl = true, scopeInvestigationId = null } = {}) {
  closeRouteScopedSurfaces();
  state.activeInvestigationId = null;
  if (state.investigationPollTimer) window.clearTimeout(state.investigationPollTimer);
  state.investigationPollTimer = null;
  const requestSerial = ++state.eventOverviewScopeRequestSerial;
  const previousScopeId = state.eventOverviewScopeInvestigationId;
  const scopeRequested = scopeInvestigationId !== null && scopeInvestigationId !== undefined;
  const requestedScopeId = scopeRequested ? String(scopeInvestigationId).trim() : null;
  const requestedScope = requestedScopeId ? state.investigations.find((item) => item.id === requestedScopeId) : null;
  if (scopeRequested && !requestedScope) {
    state.eventOverviewScopeInvestigationId = null;
    const url = new URL(window.location.href);
    ["view", "event_scope", "event", "investigation", "tab", "section"].forEach((key) => url.searchParams.delete(key));
    history.replaceState(null, "", url);
    showInvestigationHome({ syncUrl: false });
    toast("专题范围不存在或暂时不可用，已返回“我的专题”；没有改为显示全局事件。", "error", 7000);
    return false;
  }
  state.eventOverviewScopeInvestigationId = requestedScope?.id || null;
  if (previousScopeId !== state.eventOverviewScopeInvestigationId) {
    state.selectedEventRequestSerial += 1;
    state.selectedId = null;
    state.selectedEvent = null;
    state.selectedEventError = null;
    $("#btn-report").disabled = true;
    if ($("#event-drawer")?.classList.contains("open")) closeDrawer();
  }
  setRouteVisibility("classic");
  renderTopic();
  renderSources();
  renderAssessment();
  renderGaps();
  applyFilters();
  renderMetrics();
  if (syncUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "events");
    url.searchParams.delete("investigation");
    url.searchParams.delete("tab");
    url.searchParams.delete("section");
    if (state.eventOverviewScopeInvestigationId) url.searchParams.set("event_scope", state.eventOverviewScopeInvestigationId);
    else url.searchParams.delete("event_scope");
    history.pushState(null, "", url);
  }
  const scopeInvestigation = eventOverviewInvestigation();
  if (scopeInvestigation) {
    try {
      await ensureEventOverviewScopeLoaded(scopeInvestigation);
    } catch (error) {
      if (requestSerial !== state.eventOverviewScopeRequestSerial || state.eventOverviewScopeInvestigationId !== scopeInvestigation.id) return false;
      toast(`本专题事件范围读取失败：${error.message || "未知错误"}`, "error", 7000);
    }
    if (requestSerial !== state.eventOverviewScopeRequestSerial || state.eventOverviewScopeInvestigationId !== scopeInvestigation.id) return false;
    renderTopic();
    renderSources();
    renderGaps();
    applyFilters();
  }
  await reconcileEventOverviewSelection();
  if (requestSerial !== state.eventOverviewScopeRequestSerial) return false;
  renderMetrics();
  return true;
}

async function openInvestigation(investigationId, tab = "outcomes", { syncUrl = true, section = null } = {}) {
  closeRouteScopedSurfaces();
  resetEventOverviewContext();
  const investigation = state.investigations.find((item) => item.id === investigationId);
  if (!investigation || investigation.status === "archived") {
    toast(investigation?.status === "archived" ? "该专题已删除，请先在首页恢复。" : "专题不存在或已被移除。", "error");
    showInvestigationHome({ syncUrl });
    return;
  }
  const route = normalizeInvestigationRoute(tab, section);
  state.eventOverviewScopeInvestigationId = null;
  if ($("#event-drawer")?.classList.contains("open")) closeDrawer();
  state.activeInvestigationId = investigationId;
  state.activeInvestigationTab = route.tab;
  if (route.section) state.investigationSectionByTab[route.tab] = route.section;
  setRouteVisibility("investigation");
  $("#investigation-home").hidden = true;
  $("#investigation-detail-page").hidden = false;
  renderInvestigationPage();
  if (syncUrl) {
    syncInvestigationUrl(investigationId, state.activeInvestigationTab, route.section);
  } else {
    const url = new URL(window.location.href);
    const before = url.search;
    ["view", "event", "event_scope"].forEach((key) => url.searchParams.delete(key));
    if (url.search !== before) history.replaceState(null, "", url);
  }
  await loadInvestigationWorkspace(investigationId);
  if (investigationNeedsEvidence()) await loadInvestigationEvidence(investigation);
}

async function openInvestigationEvent(eventId, investigationId = state.activeInvestigationId) {
  if (!eventId) {
    toast("事件标识缺失，无法打开正式档案。", "error", 6500);
    return;
  }
  const scope = investigationId
    ? state.investigations.find((item) => item.id === investigationId) || null
    : null;
  if (investigationId && !isServerInvestigation(scope)) {
    toast("无法确定这个事件所属的专题，已停止打开。", "error", 6500);
    return;
  }
  const applied = await showClassicWorkspace({ scopeInvestigationId: scope?.id || null });
  if (!applied) return;
  clearFilters();
  if (!state.filteredEvents.some((event) => event.id === eventId)) {
    toast(scope
      ? "这个事件已不在当前专题的正式档案中，未打开其他专题的数据。"
      : "这个事件档案已不可用，请刷新后重试。", "error", 7000);
    return;
  }
  await selectEvent(eventId, { open: true });
}

function setInvestigationTab(tab, { syncUrl = true, section = null } = {}) {
  const investigation = activeInvestigation();
  if (!investigation) return;
  const route = normalizeInvestigationRoute(tab, section);
  state.activeInvestigationTab = route.tab;
  if (route.section) state.investigationSectionByTab[route.tab] = route.section;
  renderInvestigationPage();
  if (syncUrl) {
    syncInvestigationUrl(investigation.id, route.tab, route.section);
  }
  if (investigationNeedsEvidence()) loadInvestigationEvidence(investigation);
}

function setInvestigationSection(section, { syncUrl = true } = {}) {
  const tab = state.activeInvestigationTab;
  if (!INVESTIGATION_SECTIONS[tab]?.has(section)) return;
  state.investigationSectionByTab[tab] = section;
  renderInvestigationPage();
  if (syncUrl && activeInvestigation()) syncInvestigationUrl(activeInvestigation().id, tab, section);
  if (investigationNeedsEvidence()) loadInvestigationEvidence(activeInvestigation());
}

function investigationScopeLabel(investigation) {
  const mode = investigation.tracking_mode === "continuous" ? "持续关注" : "一次性研究";
  const start = investigation.event_start_at ? formatDate(investigation.event_start_at) : "不限起始时间";
  if (investigation.tracking_mode === "continuous") return `${mode} · 事件时间：${start}起`;
  const end = investigation.event_end_at ? formatDate(investigation.event_end_at) : "不限结束时间";
  return `${mode} · 事件时间：${start} 至 ${end}`;
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
      <div class="investigation-scope-line"><span>${escapeHtml(investigationScopeLabel(investigation))}</span></div>
    </div>
    <div class="investigation-page-actions">
      <button class="btn btn-ghost" type="button" data-investigation-action="search">⌕ 自动发现</button>
      <button class="btn btn-ghost" type="button" data-investigation-action="import">＋ 添加资料</button>
      <button class="btn btn-primary" type="button" data-investigation-action="review">待处理（${metrics.attention}）</button>
      ${isServerInvestigation(investigation) && investigation.sync_mode !== "system" ? '<button class="btn btn-ghost" type="button" data-investigation-action="archive-topic">删除专题</button>' : ""}
    </div>`;
  $$("[data-investigation-tab]", $("#investigation-tabs")).forEach((button) => {
    const active = button.dataset.investigationTab === state.activeInvestigationTab;
    button.classList.toggle("active", active);
    if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected", String(active));
  });
  renderInvestigationPanel(investigation);
  renderMetrics();
}

function renderInvestigationPanel(investigation) {
  const renderers = {
    overview: renderInvestigationToday,
    materials: renderInvestigationMaterials,
    review: renderInvestigationReview,
    outcomes: renderInvestigationOutcomes,
    activity: renderInvestigationActivity,
  };
  const content = (renderers[state.activeInvestigationTab] || renderers.overview)(investigation);
  const taskError = state.investigationTaskErrors.get(investigation.id);
  const queueError = taskError
    ? renderOperationalError(taskError, {
      stage: "fetch",
      actionHtml: '<button class="btn btn-ghost" type="button" data-investigation-action="refresh">重新读取完整处理队列</button>',
    })
    : "";
  const eventErrorEntry = investigationNeedsEvidence()
    ? eventsForInvestigation(investigation)
      .map((event) => [event, state.investigationEventErrors.get(event.id)])
      .find(([, error]) => error)
    : null;
  const eventError = eventErrorEntry
    ? renderOperationalError(eventErrorEntry[1], {
      stage: "fetch",
      actionHtml: '<button class="btn btn-ghost" type="button" data-investigation-action="refresh">重新读取正式事件与证据</button>',
    })
    : "";
  const activityError = state.activeInvestigationTab === "activity" && state.investigationActivityErrors.get(investigation.id)
    ? renderOperationalError(state.investigationActivityErrors.get(investigation.id), {
      stage: "fetch",
      actionHtml: '<button class="btn btn-ghost" type="button" data-investigation-action="refresh">重新读取完整操作记录</button>',
    })
    : "";
  const outcomeError = state.activeInvestigationTab === "outcomes" && state.investigationOutcomeErrors.get(investigation.id)
    ? renderOperationalError(state.investigationOutcomeErrors.get(investigation.id), {
      stage: "fetch",
      actionHtml: '<button class="btn btn-ghost" type="button" data-investigation-action="refresh">重新读取专题成果</button>',
    })
    : "";
  $("#investigation-panel").innerHTML = `${queueError}${eventError}${activityError}${outcomeError}${content}`;
}

function investigationSectionNav(tab, options) {
  const current = activeInvestigationSection(tab);
  return `<nav class="investigation-section-tabs" aria-label="${tab === "materials" ? "资料方式" : "成果类型"}">
    ${options.map(([value, label, count]) => `<button type="button" class="${current === value ? "active" : ""}" data-investigation-section="${escapeHtml(value)}">${escapeHtml(label)}${count == null ? "" : ` <span>${escapeHtml(count)}</span>`}</button>`).join("")}
  </nav>`;
}

function renderInvestigationMaterials(investigation) {
  return `${investigationPanelHeading("MATERIALS", "资料与来源", "查看已经导入的资料、固定网站和搜索记录；这里是原始材料区，不是最终结论。")}
    <div class="material-entry-grid">
      <button type="button" class="material-entry-card" data-investigation-action="import"><span>01</span><strong>添加我的资料</strong><small>网页、粘贴文本或本地文件</small></button>
      <button type="button" class="material-entry-card" data-investigation-action="add-source"><span>02</span><strong>关注固定网站</strong><small>网页或 RSS 持续采集</small></button>
      <button type="button" class="material-entry-card" data-investigation-action="search"><span>03</span><strong>自动搜索公开资料</strong><small>关键词发现并处理选中原文</small></button>
    </div>
    <div class="materials-overview-stack">${renderInvestigationDiscovery(investigation)}${renderInvestigationMonitoring(investigation)}</div>`;
}

function fallbackOutcomeForInvestigation(investigation) {
  // The dedicated outcome endpoint is authoritative.  If it is unavailable,
  // fail closed and reuse only event cards explicitly marked confirmed.
  const eventCards = eventsForInvestigation(investigation).filter((event) => event.status === "confirmed");
  const events = eventCards
    .map((event) => state.investigationEventDetails.get(event.id) || event)
    .sort((a, b) => new Date(b.updated_at || b.start_at || 0) - new Date(a.updated_at || a.start_at || 0));
  const claims = events.flatMap((event) => (event.claims || []).map((claim) => ({
    ...claim,
    event_id: event.id,
    event_title: event.title,
    evidence_count: (claim.evidence || []).length,
  })));
  const entities = [...new Map(events.flatMap((event) => event.entities || []).map((entity) => [entity.id, entity])).values()];
  const sourceIds = new Set(events.flatMap((event) => event.documents || []).map((document) => document.source?.id).filter(Boolean));
  const evidenceCount = claims.reduce((sum, claim) => sum + Number(claim.evidence_count || 0), 0);
  const latestEvent = events[0] || null;
  return {
    current_answer: latestEvent ? {
      status: "available",
      headline: `已确认 ${events.length} 个事件，当前最新进展：${latestEvent.title}`,
      text: latestEvent.assessment?.judgement || latestEvent.summary || "该事件已进入正式档案。",
      notice: "只展示已确认的正式对象。",
    } : {
      status: "empty",
      headline: "尚未形成正式成果",
      text: "资料可以继续在后台处理；只有经过人工采用的内容才会出现在这里。",
      notice: "没有使用搜索摘要或 AI 草稿填充结论。",
    },
    changes: { basis: "topic_created", label: "当前累计", since: investigation.created_at, new_event_count: events.length, updated_event_count: 0 },
    counts: {
      events: events.length,
      claims: claims.length,
      evidence: evidenceCount,
      sources: sourceIds.size,
      entities: entities.length,
      unresolved_claims: claims.filter((claim) => ["contested", "unverified"].includes(claim.status)).length,
      single_source_claims: claims.filter((claim) => claim.source_verification?.status === "single_source").length,
      multi_source_claims: claims.filter((claim) => claim.source_verification?.status === "supported").length,
      claims_without_evidence: claims.filter((claim) => !claim.evidence_count).length,
      waiting_for_review: investigationMetrics(investigation).ready,
      processing: investigationMetrics(investigation).processing,
      failed: investigationMetrics(investigation).failed,
    },
    events,
    claims,
    entities,
    information_gaps: events.flatMap((event) => event.assessment?.information_gaps || []),
    reports: reportsForInvestigation(investigation),
  };
}

function outcomeForInvestigation(investigation) {
  return state.investigationOutcomes.get(investigation.id) || fallbackOutcomeForInvestigation(investigation);
}

function outcomeStatusClass(status) {
  return ({ confirmed: "supported", supported: "supported", single_source: "unverified", contested: "contested", refuted: "refuted", unverified: "unverified" })[status] || "unverified";
}

function renderOutcomeHero(investigation, outcome) {
  const answer = outcome.current_answer || {};
  const counts = outcome.counts || {};
  const continuous = investigation.tracking_mode === "continuous";
  const empty = answer.status !== "available";
  return `<section class="outcome-hero ${empty ? "empty" : "available"}">
    <div class="outcome-hero-topline"><span>${continuous ? "持续关注" : "一次性研究"}</span><small>${continuous ? "当前成果会随人工确认持续更新" : "每次生成报告都会保留一个冻结版本"}</small></div>
    <span class="panel-kicker">CURRENT ANSWER</span>
    <h2>${escapeHtml(answer.headline || "尚未形成正式成果")}</h2>
    <p class="outcome-answer">${escapeHtml(answer.text || "只有经过人工确认的内容才会进入专题成果。")}</p>
    <p class="outcome-boundary">${escapeHtml(answer.notice || "未确认候选不会进入成果。")}</p>
    <div class="outcome-hero-actions">
      ${empty ? '<button class="btn btn-primary" type="button" data-investigation-action="review">查看待处理</button>' : '<button class="btn btn-primary" type="button" data-investigation-action="generate-report">生成当前报告</button>'}
      ${Number(counts.events || 0) >= 2 ? '<button class="btn btn-ghost" type="button" data-investigation-action="reorganize">重新整理专题</button>' : ""}
      <button class="btn btn-ghost" type="button" data-investigation-action="refresh">↻ 刷新成果</button>
      <span>${Number(counts.events || 0)} 个已确认事件 · ${Number(counts.evidence || 0)} 条证据 · ${Number(counts.sources || 0)} 个来源</span>
    </div>
  </section>`;
}

function renderOutcomeChanges(outcome) {
  const counts = outcome.counts || {};
  const changes = outcome.changes || {};
  const hasBaseline = changes.basis === "latest_report";
  return `<section class="workbench-surface outcome-changes">
    <div class="workbench-surface-head"><div><h3>${escapeHtml(changes.label || "当前累计")}</h3><p>${hasBaseline ? `从 ${formatDate(changes.since, true)} 生成的上次报告开始计算` : "尚无报告基线，显示当前已经确认的全部成果"}</p></div></div>
    <div class="outcome-change-grid">
      <div><strong>${hasBaseline ? Number(changes.new_event_count || 0) : Number(counts.events || 0)}</strong><span>${hasBaseline ? "新增事件" : "已确认事件"}</span></div>
      <div><strong>${hasBaseline ? Number(changes.updated_event_count || 0) : Number(counts.claims || 0)}</strong><span>${hasBaseline ? "更新事件" : "关键信息"}</span></div>
      <div><strong>${Number(counts.unresolved_claims || 0)}</strong><span>仍有疑点</span></div>
      <button type="button" data-investigation-action="review"><strong>${Number(counts.waiting_for_review || 0) + Number(counts.failed || 0)}</strong><span>待处理</span></button>
    </div>
    ${Number(counts.processing || 0) ? `<p class="outcome-processing-note">另有 ${Number(counts.processing)} 份资料由系统后台处理中，无需操作。</p>` : ""}
  </section>`;
}

function renderOutcomeFindings(outcome) {
  const priority = { confirmed: 0, supported: 1, single_source: 2, contested: 3, unverified: 4, refuted: 5 };
  const claims = [...(outcome.claims || [])].sort((a, b) => (priority[a.status] ?? 9) - (priority[b.status] ?? 9));
  return `<section class="workbench-surface outcome-findings" id="topic-key-findings">
    <div class="workbench-surface-head"><div><h3>关键发现与证据</h3><p>先看结论状态，需要时再展开原文依据</p></div><span class="count-badge">${claims.length}</span></div>
    ${claims.length ? `<div class="outcome-finding-list">${claims.map((claim) => {
      const evidence = claim.evidence || [];
      return `<details class="outcome-finding ${escapeHtml(outcomeStatusClass(claim.status))}">
        <summary><span class="claim-status">${escapeHtml(LABELS.claim[claim.status] || claim.status || "缺少依据")}</span><strong>${escapeHtml(claim.text || "未填写关键信息")}</strong><small>${Number(claim.source_verification?.independent_source_count || 0)} 个独立来源</small></summary>
        <div class="outcome-finding-body">
          <p class="outcome-finding-event">来自事件：${escapeHtml(claim.event_title || "未知事件")}<button class="text-btn" type="button" data-investigation-event="${escapeHtml(claim.event_id || "")}">打开事件档案</button></p>
          ${evidence.length ? evidence.map((item) => `<blockquote class="outcome-evidence ${escapeHtml(item.stance || "context")}"><p>${escapeHtml(item.snippet || "")}</p><footer><span>${escapeHtml(item.document?.source?.name || "来源未知")} · ${formatDate(item.document?.published_at)}</span><a href="${escapeHtml(withEventContext(item.snapshot_url || item.document?.snapshot_url, claim.event_id))}">查看保存的原文</a></footer></blockquote>`).join("") : '<p class="outcome-gap-note">这条信息还没有连接可定位的原文依据。</p>'}
          ${claim.status === "single_source" || claim.status === "unverified" ? `<button class="btn btn-ghost" type="button" data-investigation-find-source="${escapeHtml(claim.text || claim.event_title || "")}">搜索更多来源</button>` : ""}
        </div>
      </details>`;
    }).join("")}</div>` : '<div class="investigation-empty"><strong>还没有可展示的关键发现</strong><p>事件可以先确认；暂时没有关键信息或原文依据时会保持为空，不会自动编造。</p></div>'}
  </section>`;
}

function renderOutcomeTimeline(outcome) {
  const events = outcome.events || [];
  const timedEvents = events.filter((event) => event.start_at);
  const untimedEvents = events.filter((event) => !event.start_at);
  return `<section class="workbench-surface outcome-timeline">
    <div class="workbench-surface-head"><div><h3>事件时间线</h3><p>只包含本专题已经人工确认的事件</p></div><span class="count-badge">${events.length}</span></div>
    ${timedEvents.length ? `<div class="outcome-timeline-list">${timedEvents.map((event) => `<article><time>${formatEventDate(event.start_at)}</time><div><h4>${escapeHtml(event.title)}</h4><p>${escapeHtml(event.summary || "暂无摘要")}</p></div><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">查看依据</button></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>暂无带明确时间的事件</strong></div>'}
    ${untimedEvents.length ? `<details class="untimed-events"><summary>时间待补充（${untimedEvents.length}）</summary><div class="outcome-timeline-list">${untimedEvents.map((event) => `<article><time>时间未识别</time><div><h4>${escapeHtml(event.title)}</h4><p>${escapeHtml(event.summary || "暂无摘要")}</p></div><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">打开并补充</button></article>`).join("")}</div></details>` : ""}
  </section>`;
}

function outcomeSourceSearchSeed(investigation, outcome) {
  const gap = (outcome.information_gaps || []).find((item) => String(item || "").trim());
  const claim = (outcome.claims || []).find((item) => ["single_source", "unverified", "contested"].includes(item.status));
  return String(gap || claim?.text || investigation?.question || investigation?.title || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

function renderOutcomeEntitiesAndGaps(investigation, outcome) {
  const entities = outcome.entities || [];
  const gaps = [...new Set((outcome.information_gaps || []).map((item) => String(item).trim()).filter(Boolean))];
  const counts = outcome.counts || {};
  return `<div class="outcome-secondary-grid">
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>关键参与方</h3><p>来自已确认事件的实体关系</p></div><span class="count-badge">${entities.length}</span></div>
      ${entities.length ? `<div class="outcome-entity-list">${entities.map((entity) => `<div><strong>${escapeHtml(entity.name)}</strong><span>${escapeHtml(entity.role || entity.type || "相关")}</span></div>`).join("")}</div>` : '<div class="investigation-empty"><strong>尚未确认参与方</strong></div>'}
    </section>
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>未知与下一步</h3><p>明确说出还不知道什么</p></div></div>
      <ul class="outcome-gap-list">${gaps.map((gap) => `<li>${escapeHtml(gap)}</li>`).join("")}${Number(counts.single_source_claims || 0) ? `<li>${Number(counts.single_source_claims)} 条关键信息目前只有一个独立来源。</li>` : ""}${Number(counts.claims_without_evidence || 0) ? `<li>${Number(counts.claims_without_evidence)} 条关键信息缺少可定位的原文依据。</li>` : ""}${Number(counts.unresolved_claims || 0) ? `<li>${Number(counts.unresolved_claims)} 条关键信息存在冲突或缺少依据。</li>` : ""}</ul>
      <button class="btn btn-ghost" type="button" data-investigation-find-source="${escapeHtml(outcomeSourceSearchSeed(investigation, outcome))}">搜索更多来源</button>
      ${!gaps.length && !Number(counts.single_source_claims || 0) && !Number(counts.claims_without_evidence || 0) && !Number(counts.unresolved_claims || 0) ? '<p class="outcome-gap-note">当前没有登记明确缺口；这不等于信息已经完整。</p>' : ""}
    </section>
  </div>`;
}

function renderOutcomeReportHistory(investigation, outcome) {
  const reports = outcome.reports || [];
  const canGenerate = Number(outcome.counts?.events || 0) > 0;
  return `<section class="workbench-surface outcome-reports">
    <div class="workbench-surface-head"><div><h3>报告与历史版本</h3><p>报告只使用生成时已经确认的正式成果</p></div><button class="btn btn-primary" type="button" data-investigation-action="generate-report" ${canGenerate ? "" : "disabled"}>生成当前报告</button></div>
    ${reports.length ? `<div class="outcome-report-list">${reports.map((report, index) => `<article><div><span>${index ? "历史版本" : "最新版本"}</span><h4>${escapeHtml(report.title || `PLDR 专题报告：${investigation.title}`)}</h4><p>${formatDate(report.generated_at || report.created_at, true)} · ${escapeHtml(report.event_count ?? "未知")} 个事件 · ${escapeHtml(report.evidence_count ?? "未知")} 条证据</p></div>${report.url ? `<a class="btn btn-ghost" href="${escapeHtml(report.url)}">打开报告</a>` : ""}</article>`).join("")}</div>` : '<div class="investigation-empty"><strong>尚未生成报告</strong><p>专题成果会持续显示；生成报告后会留下可回看的冻结版本。</p></div>'}
  </section>`;
}

function renderInvestigationOutcomes(investigation) {
  const outcome = outcomeForInvestigation(investigation);
  return `<div class="topic-outcome-page">
    ${renderOutcomeHero(investigation, outcome)}
    ${renderOutcomeChanges(outcome)}
    ${renderOutcomeFindings(outcome)}
    ${renderOutcomeTimeline(outcome)}
    ${renderOutcomeEntitiesAndGaps(investigation, outcome)}
    ${renderOutcomeReportHistory(investigation, outcome)}
  </div>`;
}

function investigationPanelHeading(eyebrow, title, description, actions = "") {
  return `<div class="investigation-panel-heading"><div><span class="panel-kicker">${escapeHtml(eyebrow)}</span><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div>${actions ? `<div class="investigation-panel-actions">${actions}</div>` : ""}</div>`;
}

function taskFailureActionLabel(error) {
  if (["generate", "model"].includes(error?.stage)) return "重新分析";
  if (["fetch", "parse", "extract"].includes(error?.stage)) return "重试抓取";
  return "重试处理";
}

function taskProgressText(stage, task = null) {
  if (stage === "queued" && task?.waiting_for_model_retry) {
    return "AI 分析本次未完成，已自动重新排队；后台会继续处理，无需手动操作。";
  }
  return ({
    ready: "草稿已经准备好，核对并明确确认前不会写入正式档案。",
    generating: "原始材料已保存，AI 正在分析正文。",
    fetching: "正在保存原始页面；搜索摘要不会进入证据链。",
    queued: "任务已进入处理队列，尚未开始抓取。",
    accepted: "已由人工确认并保存到正式档案。",
    rejected: "已决定不采用，正式档案没有改变。",
    cancelled: "处理已结束，正式档案没有改变。",
    completed: "处理已经完成。",
  })[stage] || "进度来自服务端。";
}

function renderTaskDegradation(degradation) {
  const message = degradation?.message || degradation?.display_message || "原文已保存；基础草稿可能不完整，请逐项核对。";
  const detail = degradation?.next_action || degradation?.technical_detail || degradation?.error || degradation?.title || "模型未返回完整草稿，系统仅保留了可追溯的基础摘录。";
  return `<div class="task-degradation" role="status">
    <strong>基础草稿 · 需核对</strong>
    <span>${escapeHtml(message)}</span>
    <details><summary>为什么是基础草稿？</summary><small>${escapeHtml(detail)}</small></details>
  </div>`;
}

function renderTaskRows(tasks, emptyMessage = "当前没有待处理任务。") {
  if (!tasks.length) return `<div class="investigation-empty"><strong>${escapeHtml(emptyMessage)}</strong><p>这里不会用演示状态填充。</p></div>`;
  return `<div class="topic-task-list">${tasks.map((task) => {
    const stage = canonicalTaskStage(task);
    const intakeId = taskIntakeId(task);
    const taskId = task.id || task.task_id;
    const canReview = stage === "ready" && intakeId;
    const errorPayload = stage === "failed" ? taskErrorPayload(task) : null;
    const normalizedError = errorPayload ? normalizeOperationalError(errorPayload, errorPayload.stage || "unknown") : null;
    const retryAllowed = normalizedError?.retryable ?? task.error?.retryable ?? task.retryable ?? true;
    const canRetryTask = retryAllowed && (stage === "failed" || task.retryable === true) && taskId && !String(taskId).startsWith("intake:") && !String(taskId).startsWith("unassigned:");
    const intake = task.intake_item || state.intakeItems.find((item) => item.id === intakeId);
    let primaryAction = "";
    if (canReview) {
      primaryAction = `<button class="btn btn-primary" type="button" data-investigation-action="open-review" data-intake-id="${escapeHtml(intakeId)}">查看并决定</button>`;
    } else if (canRetryTask) {
      primaryAction = `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-task" data-task-id="${escapeHtml(taskId)}">${escapeHtml(taskFailureActionLabel(normalizedError))}</button>`;
    } else if (retryAllowed && stage === "failed" && intake?.status === "generation_failed") {
      primaryAction = `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-intake" data-intake-id="${escapeHtml(intakeId)}">重新分析</button>`;
    } else if (retryAllowed && stage === "failed" && intake?.search?.result_id) {
      primaryAction = `<button class="btn btn-ghost warning" type="button" data-investigation-action="retry-search" data-search-result-id="${escapeHtml(intake.search.result_id)}" data-intake-id="${escapeHtml(intakeId)}">重试抓取</button>`;
    } else if (stage === "failed") {
      primaryAction = '<button class="btn btn-ghost" type="button" data-expand-error>查看解决办法</button>';
    }
    const canRemoveFromInvestigation = stage === "failed"
      && intakeId
      && recordAllowsAction(task, "remove_from_investigation", "remove");
    const secondaryAction = canRemoveFromInvestigation
      ? `<button class="text-btn task-secondary-action" type="button" data-investigation-action="remove-task" data-task-id="${escapeHtml(taskId || "")}" data-intake-id="${escapeHtml(intakeId)}" title="移到本专题已删除，可恢复">删除</button>`
      : "";
    const degradation = task.degradation || (task.degraded && task.error ? task.error : null);
    const relevance = task.topic_relevance ? searchResultRelevance(task) : null;
    return `
      <article class="topic-task-row">
        <div>
          <h3>${escapeHtml(taskTitle(task))}</h3>
          ${errorPayload ? renderOperationalError(errorPayload, { stage: errorPayload.stage || "unknown", compact: true, actionHtml: primaryAction }) : degradation ? renderTaskDegradation(degradation) : `<p>${escapeHtml(taskProgressText(stage, task))}</p>`}
          <div class="topic-task-meta">${relevance ? `<span class="search-relevance ${escapeHtml(relevance.level)}" title="${escapeHtml(relevance.reason)}">${escapeHtml(relevance.label)}</span>` : ""}${taskStatusMarkup(stage, task)}<span>${formatDate(task.updated_at || task.created_at || task.queued_at, true)}</span></div>
        </div>
        <div class="topic-task-actions">
          ${errorPayload ? "" : primaryAction}
          ${secondaryAction}
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

function renderSituationAssessment(investigation) {
  const events = eventsForInvestigation(investigation);
  const tasks = tasksForInvestigation(investigation);
  const details = events.map((event) => state.investigationEventDetails.get(event.id)).filter(Boolean);
  const documents = details.flatMap((event) => event.documents || []);
  const claims = details.flatMap((event) => event.claims || []);
  const evidence = claims.flatMap((claim) => claim.evidence || []);
  const claimsWithEvidence = claims.filter((claim) => Array.isArray(claim.evidence) && claim.evidence.length > 0).length;
  const sourceTiers = documents.map((document) => Number(document.source?.reliability_tier)).filter(Number.isFinite);
  const corroboratedEvents = events.filter((event) => Number(event.independent_source_count || 0) >= 2).length;
  const unresolvedClaims = events.reduce((sum, event) => sum + Number(event.claim_counts?.contested || 0) + Number(event.claim_counts?.unverified || 0), 0);
  const assessments = details.map((event) => event.assessment).filter(Boolean);
  const humanAssessments = assessments.filter((assessment) => /human|analyst|人工|分析员/i.test(String(assessment.generated_by || "")));
  const failedTasks = tasks.filter((task) => canonicalTaskStage(task) === "failed").length;
  const sourceFailures = targetsForInvestigation(investigation).filter((target) => ["error", "degraded", "stale"].includes(collectionTargetStatus(target))).length;
  const latestTime = [...tasks.map((task) => task.updated_at || task.created_at), ...events.map((event) => event.updated_at || event.start_at)]
    .filter(Boolean)
    .sort((a, b) => new Date(b) - new Date(a))[0];

  let reliability = { label: "未知", tone: "unknown", basis: "尚未读取到本专题已确认资料的来源档案。" };
  if (sourceTiers.length) {
    const bestTier = Math.min(...sourceTiers);
    reliability = {
      label: bestTier <= 2 ? "含较高等级来源" : "仍需人工评估",
      tone: bestTier <= 2 ? "supported" : "attention",
      basis: `${documents.length} 篇已确认资料带有来源级别记录；T${bestTier} 是其中最高等级，不等于具体主张为真。`,
    };
  } else if (events.length) {
    reliability.basis = "已有正式事件，但当前列表没有加载来源级别；保持未知，不从站点名称猜测。";
  }

  const corroboration = events.length ? {
    label: corroboratedEvents ? `${corroboratedEvents} 个事件有独立印证` : "以单一来源为主",
    tone: corroboratedEvents ? "supported" : "attention",
    basis: `${events.length} 个正式事件中，${corroboratedEvents} 个连接了至少 2 个独立来源组。文档数量不会冒充独立来源数量。`,
  } : { label: "未知", tone: "unknown", basis: "尚无正式事件，不能判断独立来源印证情况。" };

  const everyClaimHasEvidence = claims.length > 0 && claimsWithEvidence === claims.length;
  const evidenceCoverage = details.length === events.length && events.length ? {
    label: everyClaimHasEvidence ? "已逐条回链" : "存在证据缺口",
    tone: everyClaimHasEvidence ? "supported" : "attention",
    basis: `${claims.length} 条已确认主张中 ${claimsWithEvidence} 条至少连接一条固定快照证据，共 ${evidence.length} 条证据；仍需逐条核对，不是可信度分数。`,
  } : {
    label: events.length ? "待核对" : "未知",
    tone: "unknown",
    basis: events.length ? "请打开“主张与证据”读取正式对象后核对；当前不根据事件数量推算完整度。" : "尚无正式事件与主张，无法判断证据完整性。",
  };

  const humanConfidence = humanAssessments.length ? {
    label: "已有人工研判",
    tone: "supported",
    basis: `${humanAssessments.length} 个正式事件带有明确标记的人工研判；这里不把机器候选置信度混入。`,
  } : {
    label: "未知",
    tone: "unknown",
    basis: assessments.length ? "当前研判未明确标记为人工完成，因此不展示人工置信度。" : "尚未读取到明确的人工研判记录。",
  };

  const rows = [
    ["来源可靠性", reliability],
    ["独立来源印证", corroboration],
    ["证据完整性", evidenceCoverage],
    ["人工研判置信度", humanConfidence],
  ];
  return `
    <section class="workbench-surface situation-assessment" aria-labelledby="situation-assessment-title">
      <div class="workbench-surface-head">
        <div><h3 id="situation-assessment-title">当前研判</h3><p>分项说明依据，不生成综合分</p></div>
        <span class="situation-updated">${latestTime ? `更新 ${formatDate(latestTime, true)}` : "尚无更新时间"}</span>
      </div>
      <div class="situation-dimensions">
        ${rows.map(([name, value]) => `<details class="situation-dimension ${escapeHtml(value.tone)}"><summary><span>${escapeHtml(name)}</span><strong>${escapeHtml(value.label)}</strong></summary><p>${escapeHtml(value.basis)}</p></details>`).join("")}
      </div>
      <div class="situation-gaps">
        <strong>需要注意</strong>
        <span>${unresolvedClaims ? `${unresolvedClaims} 条争议或待核实主张` : "未读取到争议主张"}</span>
        <span>${failedTasks ? `${failedTasks} 个处理失败` : "处理队列无失败"}</span>
        <span>${sourceFailures ? `${sourceFailures} 个监测来源异常` : "已关联监测来源无已知异常"}</span>
      </div>
      <div class="situation-actions"><button class="text-btn" type="button" data-investigation-action="claims">查看主张与证据</button><button class="text-btn" type="button" data-investigation-action="review">处理并确认入档</button></div>
    </section>`;
}

function renderInvestigationToday(investigation) {
  const tasks = tasksForInvestigation(investigation);
  const active = tasks.filter((task) => taskIsActive(task) && taskBelongsInPending(task));
  const heldForDiscovery = tasks.filter((task) => taskIsActive(task) && !taskBelongsInPending(task));
  const readyTasks = active.filter((task) => canonicalTaskStage(task) === "ready");
  const failedTasks = active.filter((task) => canonicalTaskStage(task) === "failed");
  const processingTasks = active.filter((task) => ["queued", "fetching", "generating"].includes(canonicalTaskStage(task)));
  const attention = readyTasks.length + failedTasks.length;
  return `${investigationPanelHeading("PENDING", "待处理", "这里只放需要你作决定或恢复的内容。系统正在处理的资料不要求你操作。", `<button class="btn btn-ghost" type="button" data-investigation-action="refresh">↻ 刷新</button>`)}
    <div class="investigation-stats">
      <div class="investigation-stat"><span>等待确认</span><strong>${readyTasks.length}</strong><small>可多选加入专题或忽略</small></div>
      <div class="investigation-stat"><span>需要处理</span><strong>${failedTasks.length}</strong><small>看清原因后重试或删除</small></div>
      <div class="investigation-stat"><span>系统处理中</span><strong>${processingTasks.length}</strong><small>无需操作，完成后自动出现</small></div>
      <div class="investigation-stat"><span>本轮待办</span><strong>${attention}</strong><small>${attention ? "处理完即可离开" : "当前无需人工操作"}</small></div>
    </div>
    <div class="attention-queue-stack">
      <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>等待确认</h3><p>可打开单条查看，也可在待处理窗口中多选处理</p></div><span class="count-badge warning">${readyTasks.length}</span></div>${renderTaskRows(readyTasks, "本轮没有需要确认的内容。")}</section>
      <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>需要处理</h3><p>失败原因、影响和解决办法会直接显示</p></div><span class="count-badge warning">${failedTasks.length}</span></div>${renderTaskRows(failedTasks, "当前没有处理失败的内容。")}</section>
      <details class="processing-queue" ${processingTasks.length ? "" : "open"}><summary><span>系统正在处理</span><strong>${processingTasks.length}</strong><small>无需操作</small></summary>${renderTaskRows(processingTasks, "系统当前没有后台处理任务。")}</details>
      ${heldForDiscovery.length ? `<section class="workbench-surface discovery-hold-note"><div><strong>${heldForDiscovery.length} 条线索未进入待处理</strong><p>系统认为这些内容相关性存疑或可能无关，已放在“资料与来源 → 发现资料”中，不要求你逐条清理。</p></div><button class="btn btn-ghost" type="button" data-investigation-action="open-discovery">查看候选线索</button></section>` : ""}
    </div>`;
}

function renderInvestigationDiscovery(investigation) {
  const detail = state.investigationDetails.get(investigation.id) || {};
  const queries = Array.isArray(detail.search_queries) ? detail.search_queries : [];
  const tasks = tasksForInvestigation(investigation).filter((task) => {
    const intake = state.intakeItems.find((item) => item.id === taskIntakeId(task));
    return task.subject_type === "search_result" || task.task_type === "search_result_intake" || intake?.input_type === "search";
  });
  return `${investigationPanelHeading("DISCOVERY", "发现资料", "外部搜索只产生候选线索；选中后才抓取原页并进入专题处理队列。", `<button class="btn btn-primary" type="button" data-investigation-action="search">⌕ 发起关键词发现</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>搜索记录与候选线索</h3><p>未通过相关性初筛的结果仍保留在这里，不会自动塞进待处理</p></div><span class="count-badge">${queries.length}</span></div>
      ${queries.length ? `<div class="topic-task-list">${queries.map((query) => `<article class="topic-task-row"><div><h3>${escapeHtml(query.keyword || "未命名查询")}</h3><p>${Number(query.result_count || 0)} 条候选结果 · ${formatDate(query.created_at, true)}</p></div><div class="topic-task-actions"><button class="btn btn-ghost" type="button" data-investigation-action="open-search-run" data-search-run-id="${escapeHtml(query.id)}">查看全部候选</button></div></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>该专题还没有搜索记录</strong><p>发起关键词发现后，所有候选结果都会保留在这里。</p></div>'}
    </section>
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>最近发现与处理</h3><p>搜索标题、摘要和排名不是 Evidence</p></div><span class="count-badge">${tasks.length}</span></div>${renderTaskRows(tasks, "该专题还没有关键词发现任务。")}</section>`;
}

function renderInvestigationMonitoring(investigation) {
  const targets = targetsForInvestigation(investigation);
  return `${investigationPanelHeading("RELIABLE COLLECTION", "监测来源", "固定网页与 RSS 的队列、运行、不可变材料和失败恢复；配置本身不是 Source/Evidence。", `<button class="btn btn-primary" type="button" data-investigation-action="add-source">＋ 添加可靠来源</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>专题受管来源</h3><p>点击来源进入完整运行与版本视图</p></div><span class="count-badge">${targets.length}</span></div>
      ${targets.length ? `<div class="topic-task-list">${targets.map((target) => {
        const status = collectionTargetStatus(target);
        return `<article class="topic-task-row"><div><h3>${escapeHtml(target.name || "未命名来源")}</h3><p>${escapeHtml(target.url || target.canonical_url || "地址未知")}</p><div class="topic-task-meta"><span class="task-stage ${escapeHtml(status === "healthy" ? "ready" : status === "pending" ? "queued" : status)}">${escapeHtml(LABELS.collectionStatus[status] || status)}</span><span>${escapeHtml(collectionTargetCount(target))}</span><span>${escapeHtml(collectionIntervalMinutes(target) ?? "?")} 分钟</span></div></div><div class="topic-task-actions"><button class="btn btn-ghost" type="button" data-investigation-action="open-source" data-target-id="${escapeHtml(target.id)}">打开运行记录</button></div></article>`;
      }).join("")}</div>` : '<div class="investigation-empty"><strong>该专题还没有关联可靠来源</strong><p>添加一个无需登录的公共 HTTP(S) 网页或 RSS，运行与材料会如实显示。</p></div>'}
    </section>`;
}

function renderInvestigationReview(investigation) {
  const tasks = tasksForInvestigation(investigation).filter((task) => taskIsActive(task) && taskBelongsInPending(task));
  return `${investigationPanelHeading("HUMAN REVIEW BOUNDARY", "待审核", "草稿准备好后才能核对；保存到正式档案前必须预览并由你确认。", `<button class="btn btn-ghost" type="button" data-investigation-action="open-intake">查看全部材料</button>`)}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>处理队列</h3><p>待核对和需要处理的材料优先，处理中材料会自动更新</p></div><span class="count-badge warning">${tasks.length}</span></div>${renderTaskRows(tasks)}</section>`;
}

function renderInvestigationEvents(investigation) {
  const events = eventsForInvestigation(investigation);
  return `${investigationPanelHeading("CONFIRMED TIMELINE", "已确认事件", "这里只列出已确认事件；地图降为有坐标事件的辅助入口。", `<button class="btn btn-ghost" type="button" data-investigation-action="classic">打开本专题事件总览</button>`)}
    <div class="investigation-events-grid">
      <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>已确认事件</h3><p>按开始时间排列</p></div><span class="count-badge">${events.length}</span></div>
        ${events.length ? `<div class="investigation-record-list">${[...events].sort((a, b) => new Date(a.start_at || 0) - new Date(b.start_at || 0)).map((event) => `<article class="investigation-record"><time>${formatDate(event.start_at)}</time><div><h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(event.summary || "暂无摘要")}</p></div><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">打开档案</button></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>该专题还没有已确认事件</strong><p>AI 候选不会在人工确认前出现在这里。</p></div>'}
      </section>
      ${renderMiniMap(investigation)}
    </div>`;
}

function invalidateInvestigationEvidence(eventIds) {
  eventIds.forEach((eventId) => {
    state.investigationEventRequestTokens.set(eventId, (state.investigationEventRequestTokens.get(eventId) || 0) + 1);
    state.investigationEventDetails.delete(eventId);
    state.investigationEventErrors.delete(eventId);
  });
}

async function loadInvestigationEvidence(investigation, { force = false } = {}) {
  const events = eventsForInvestigation(investigation);
  if (force) invalidateInvestigationEvidence(events.map((event) => event.id));
  const missing = events.filter((event) => !state.investigationEventDetails.has(event.id));
  if (!missing.length) {
    if (investigationShowsClaims()) renderInvestigationPage();
    return;
  }
  if (investigationShowsClaims()) $("#investigation-panel").innerHTML = '<div class="investigation-empty"><strong>正在读取已确认主张与固定证据快照…</strong></div>';
  for (let offset = 0; offset < missing.length; offset += 20) {
    const batch = missing.slice(offset, offset + 20);
    const requests = batch.map((event) => {
      const token = (state.investigationEventRequestTokens.get(event.id) || 0) + 1;
      state.investigationEventRequestTokens.set(event.id, token);
      return { event, token };
    });
    const results = await Promise.allSettled(requests.map(({ event }) => api(`/pldr-api/v1/events/${encodeURIComponent(event.id)}`)));
    results.forEach((result, index) => {
      const { event, token } = requests[index];
      const eventId = event.id;
      if (state.investigationEventRequestTokens.get(eventId) !== token) return;
      if (result.status === "fulfilled") {
        state.investigationEventDetails.set(eventId, result.value);
        state.investigationEventErrors.delete(eventId);
      } else {
        state.investigationEventErrors.set(eventId, result.reason);
      }
    });
  }
  if (state.activeInvestigationId === investigation.id && investigationNeedsEvidence()) renderInvestigationPage();
}

function renderInvestigationClaims(investigation) {
  const events = eventsForInvestigation(investigation);
  const details = events.map((event) => state.investigationEventDetails.get(event.id)).filter(Boolean);
  const detailErrors = events.filter((event) => state.investigationEventErrors.has(event.id));
  if (events.length && details.length < events.length && !detailErrors.length) {
    return '<div class="investigation-empty"><strong>正在读取已确认主张与证据…</strong><p>只读取正式事件接口，不会把候选补成证据。</p></div>';
  }
  const claims = details.flatMap((event) => (event.claims || []).map((claim) => ({ event, claim })));
  return `${investigationPanelHeading("CLAIMS & EVIDENCE", "主张与证据", "Evidence 固定回链到保存的快照；来源链接与搜索摘要不能替代快照。")}
    <div class="claims-stack">${claims.length ? claims.map(({ event, claim }) => `
      <article class="investigation-claim ${escapeHtml(claim.status || "unverified")}">
        <span class="task-stage ${claim.status === "confirmed" ? "ready" : claim.status === "contested" ? "generating" : "completed"}">${escapeHtml(LABELS.claim[claim.status] || claim.status || "待核实")}</span>
        <h3>${escapeHtml(claim.text)}</h3>
        ${(claim.evidence || []).map((evidence) => `<blockquote>${escapeHtml(evidence.snippet)}<footer><span>${escapeHtml(evidence.document?.source?.name || "来源未知")} · ${formatDate(evidence.document?.published_at)}</span><a href="${escapeHtml(withEventContext(evidence.snapshot_url || evidence.document?.snapshot_url, event.id))}">打开固定快照</a></footer></blockquote>`).join("") || '<p class="muted">该主张尚未连接原文证据。</p>'}
        <footer><span>事件：${escapeHtml(event.title)}</span><button class="text-btn" type="button" data-investigation-event="${escapeHtml(event.id)}">打开事件档案</button></footer>
      </article>`).join("") : detailErrors.length ? '<div class="investigation-empty"><strong>部分正式事件详情读取失败</strong><p>没有用候选补齐主张与证据；请按上方错误提示重试。</p></div>' : '<div class="investigation-empty"><strong>该专题还没有已确认主张</strong><p>候选不会提前出现在这里。</p></div>'}</div>`;
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
      ${reports.length ? reports.map((report) => `<article class="report-card"><div><h3>${escapeHtml(report.title || `PLDR 专题报告：${investigation.title}`)}</h3><p>${formatDate(report.created_at || report.generated_at, true)} · ${escapeHtml(report.evidence_count ?? "未知")} 条证据</p></div>${report.url ? `<a class="btn btn-ghost" href="${escapeHtml(report.url)}">打开报告</a>` : '<span class="muted">未返回可打开链接</span>'}</article>`).join("") : `<div class="investigation-empty"><strong>尚未生成专题报告</strong><p>${canGenerate ? "不会用模板报告冒充生成结果。" : "请先人工确认至少一个事件；候选不能用于生成正式专题报告。"}</p></div>`}
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
  if (actor === "analyst") return "用户操作";
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
  return `${investigationPanelHeading("HISTORY", "操作记录", "这里按时间记录专题中的主要操作。")}
    <section class="workbench-surface"><div class="workbench-surface-head"><div><h3>专题活动</h3><p>按时间倒序</p></div><span class="count-badge">${activities.length}</span></div>
      ${activities.length ? `<div class="investigation-record-list">${activities.map((entry) => `<article class="investigation-record"><time>${formatDate(entry.created_at, true)}</time><div><h3>${escapeHtml(activityActionLabel(entry))}</h3><p>${escapeHtml(entry.message || entry.summary || activityDetailSummary(entry))}</p></div><span class="sync-badge ${entry.local_only || entry.derived ? "local" : "server"}">${escapeHtml(activityActorLabel(entry))}</span></article>`).join("")}</div>` : '<div class="investigation-empty"><strong>暂无操作记录</strong><p>没有记录时保持为空，不会补写示例活动。</p></div>'}
    </section>`;
}

function syncInvestigationModeFields() {
  const continuous = $('input[name="investigation-create-mode"]:checked')?.value === "continuous";
  $("#investigation-create-event-end-field").hidden = continuous;
  $("#investigation-create-event-start-label").textContent = continuous ? "关注起点（可选）" : "事件开始时间（可选）";
  if (continuous) {
    $("#investigation-create-event-end").value = "";
    $("#investigation-create-event-end").setCustomValidity("");
  }
}

function suggestedInvestigationQuestion(title) {
  const cleaned = String(title || "").trim();
  return cleaned ? `关于“${cleaned}”，目前发生了什么，哪些信息已有公开资料支持，还有哪些需要继续确认？` : "";
}

function syncInvestigationStarterSuggestions() {
  const title = $("#investigation-create-name").value.trim();
  const question = $("#investigation-create-question");
  const keyword = $("#investigation-create-keyword");
  if (!question.value.trim() || question.dataset.autoSuggested === "true") {
    question.value = suggestedInvestigationQuestion(title);
    question.dataset.autoSuggested = "true";
    $("#investigation-create-question-note").textContent = title
      ? "已根据专题名称生成建议，你可以直接修改。"
      : "填写专题名称后，系统会建议一个核心问题。";
  }
  if (!keyword.value.trim() || keyword.dataset.autoSeeded === "true") {
    keyword.value = title;
    keyword.dataset.autoSeeded = "true";
  }
}

function dateInputToIso(value, endOfDay = false) {
  if (!value) return null;
  return `${value}T${endOfDay ? "23:59:59" : "00:00:00"}Z`;
}

function utcIsoFromParts(year, month, day, hour = 0, minute = 0, second = 0) {
  const values = [year, month, day, hour, minute, second].map(Number);
  const date = new Date(Date.UTC(values[0], values[1] - 1, values[2], values[3], values[4], values[5]));
  if (
    date.getUTCFullYear() !== values[0]
    || date.getUTCMonth() + 1 !== values[1]
    || date.getUTCDate() !== values[2]
    || date.getUTCHours() !== values[3]
    || date.getUTCMinutes() !== values[4]
    || date.getUTCSeconds() !== values[5]
  ) return null;
  return date.toISOString().replace(".000Z", "Z");
}

function normalizeEventTimeForConfirmation(value, { strict = true } = {}) {
  const cleaned = String(value || "").trim();
  if (!cleaned) return null;
  const chinese = cleaned.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(?:(\d{1,2})时(?:(\d{1,2})分)?(?:(\d{1,2})秒)?|(\d{1,2}):(\d{2})(?::(\d{2}))?))?$/);
  let normalized = chinese
    ? utcIsoFromParts(
      chinese[1], chinese[2], chinese[3],
      chinese[4] || chinese[7] || 0,
      chinese[5] || chinese[8] || 0,
      chinese[6] || chinese[9] || 0,
    )
    : null;
  const dateOnly = cleaned.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!normalized && dateOnly) {
    normalized = utcIsoFromParts(dateOnly[1], dateOnly[2], dateOnly[3]);
  }
  const localDateTime = cleaned.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!normalized && localDateTime) {
    normalized = utcIsoFromParts(
      localDateTime[1], localDateTime[2], localDateTime[3],
      localDateTime[4], localDateTime[5], localDateTime[6] || 0,
    );
  }
  const zonedDateTime = cleaned.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/i);
  if (!normalized && zonedDateTime && utcIsoFromParts(
    zonedDateTime[1], zonedDateTime[2], zonedDateTime[3],
    zonedDateTime[4], zonedDateTime[5], zonedDateTime[6] || 0,
  )) {
    const parsed = new Date(cleaned);
    if (!Number.isNaN(parsed.getTime())) normalized = parsed.toISOString();
  }
  if (!normalized && strict) {
    throw new Error("事件时间格式无法识别。请填写日期，例如 2026-08-22；无法确定时请留空。");
  }
  return normalized;
}

function investigationCreateFields() {
  const trackingMode = $('input[name="investigation-create-mode"]:checked')?.value || "continuous";
  return {
    title: $("#investigation-create-name").value,
    question: $("#investigation-create-question").value,
    description: $("#investigation-create-description").value,
    tracking_mode: trackingMode,
    event_start_at: dateInputToIso($("#investigation-create-event-start").value),
    event_end_at: trackingMode === "continuous" ? null : dateInputToIso($("#investigation-create-event-end").value, true),
    settings: {
      source_language: "auto",
      report_language: "zh-CN",
      publication_window: "all",
      auto_select_limit: 10,
    },
    starter_keyword: $("#investigation-create-keyword").value.trim(),
    starter_urls: $("#investigation-create-source-urls").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
    starter_text: $("#investigation-create-text").value.trim(),
    starter_files: [...$("#investigation-create-files").files],
  };
}

function validateInvestigationCreate() {
  syncInvestigationStarterSuggestions();
  const fields = [$("#investigation-create-name"), $("#investigation-create-question")];
  const invalid = fields.find((field) => !field.reportValidity());
  if (invalid) return false;
  const start = $("#investigation-create-event-start").value;
  const end = $("#investigation-create-event-end").value;
  if (start && end && end < start) {
    $("#investigation-create-event-end").setCustomValidity("结束时间不能早于开始时间");
    $("#investigation-create-event-end").reportValidity();
    return false;
  }
  $("#investigation-create-event-end").setCustomValidity("");
  const urlsField = $("#investigation-create-source-urls");
  const urls = urlsField.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  const invalidUrl = urls.find((value) => {
    try {
      return !["http:", "https:"].includes(new URL(value).protocol);
    } catch (_) {
      return true;
    }
  });
  urlsField.setCustomValidity(invalidUrl ? `无法识别这个公开地址：${invalidUrl}` : "");
  if (invalidUrl) {
    urlsField.reportValidity();
    return false;
  }
  const textField = $("#investigation-create-text");
  textField.setCustomValidity(textField.value.trim() && textField.value.trim().length < 10 ? "粘贴资料至少需要 10 个字符。" : "");
  if (!textField.reportValidity()) return false;
  return true;
}

function starterUrlLooksLikeFeed(value) {
  try {
    const parsed = new URL(value);
    return /(?:\.(?:rss|atom|xml)|\/(?:feed|rss|atom)\/?$)/i.test(parsed.pathname)
      || /(?:^|[?&])format=(?:rss|atom|xml)(?:&|$)/i.test(parsed.search);
  } catch (_) {
    return false;
  }
}

function starterItems(payload) {
  return payload?.intake_items || [payload?.intake_item].filter(Boolean);
}

function setInvestigationCreateProgress(message, type = "") {
  const result = $("#investigation-create-result");
  result.className = `import-result ${type}`.trim();
  result.textContent = message;
}

async function linkStarterItems(investigation, items, origin, errors) {
  if (!items.length) return;
  try {
    const association = await associateInvestigationObjects(investigation, "intake", items.map((item) => item.id), { origin });
    if (association.failed) errors.push(`${association.failed} 条资料已经保存，但没有成功加入专题`);
  } catch (error) {
    errors.push(`资料已经保存，但加入专题失败：${error.message || "未知错误"}`);
  }
}

async function startInitialTopicCollection(investigation, fields) {
  const keyword = fields.starter_keyword || fields.title.trim();
  const messages = [];
  const errors = [];
  let importedCount = 0;
  if (keyword && isServerInvestigation(investigation)) {
    setInvestigationCreateProgress("专题已创建，正在搜索公开资料…");
    try {
      const searchPayload = await api(API_ROUTES.search, {
        method: "POST",
        body: JSON.stringify({ keyword, scope: "news", language: detectSearchLanguage(keyword), limit: 10, page_size: 10, page: 1, investigation_id: investigation.id }),
      });
      const results = searchPayloadResults(searchPayload);
      const candidates = results
        .filter((result) => result.topic_relevance?.level === "likely")
        .slice(0, fields.settings.auto_select_limit);
      if (candidates.length) {
        await api(API_ROUTES.searchSelect, {
          method: "POST",
          body: JSON.stringify({ result_ids: candidates.map((result) => result.id), request_id: makeClientId("topic-onboarding"), investigation_id: investigation.id, actor: "analyst" }),
        });
        messages.push(`已搜索“${keyword}”，${candidates.length} 条明确相关线索已进入处理；其余 ${Math.max(0, results.length - candidates.length)} 条保留在“发现资料”`);
      } else {
        messages.push(`已完成“${keyword}”搜索；当前没有明确相关线索，结果已保留在“发现资料”，未塞入待处理`);
      }
    } catch (error) {
      errors.push(`关键词搜索未启动：${error.message || "未知错误"}`);
    }
  } else if (keyword) {
    errors.push("当前专题只保存在浏览器，关键词搜索未启动");
  }

  for (const [index, sourceUrl] of fields.starter_urls.entries()) {
    setInvestigationCreateProgress(`正在保存公开地址（${index + 1}/${fields.starter_urls.length}）…`);
    try {
      const hostname = new URL(sourceUrl).hostname;
      const feed = starterUrlLooksLikeFeed(sourceUrl);
      const payload = await api(`${feed ? "/pldr-api/v1/import/rss" : "/pldr-api/v1/import/url"}?defer_candidates=true`, {
        method: "POST",
        body: JSON.stringify(feed
          ? { url: sourceUrl, source_name: hostname || "公开 RSS", language: "auto" }
          : { url: sourceUrl, source_name: hostname || null, language: "auto" }),
      });
      const items = starterItems(payload);
      importedCount += items.length;
      await linkStarterItems(investigation, items, feed ? "topic_onboarding_rss" : "topic_onboarding_url", errors);
      items.filter((item) => item.status === "failed").forEach((item) => errors.push(`${hostname || sourceUrl} 处理失败：${item.error || "未取得有效正文"}`));
    } catch (error) {
      errors.push(`公开地址未保存：${error.message || "未知错误"}`);
    }
  }

  if (fields.starter_text) {
    setInvestigationCreateProgress("正在保存粘贴资料…");
    try {
      const payload = await api("/pldr-api/v1/intake/text?defer_candidates=true", {
        method: "POST",
        body: JSON.stringify({
          text: fields.starter_text,
          source_description: `${fields.title.trim()} · 用户粘贴资料`,
          title: `${fields.title.trim()} · 补充资料`,
          published_at: null,
          language: "auto",
        }),
      });
      const items = starterItems(payload);
      importedCount += items.length;
      await linkStarterItems(investigation, items, "topic_onboarding_text", errors);
      items.filter((item) => item.status === "failed").forEach((item) => errors.push(`粘贴资料处理失败：${item.error || "未取得有效正文"}`));
    } catch (error) {
      errors.push(`粘贴资料未保存：${error.message || "未知错误"}`);
    }
  }

  for (const [index, file] of fields.starter_files.entries()) {
    setInvestigationCreateProgress(`正在保存本地文件（${index + 1}/${fields.starter_files.length}）…`);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("source_description", `${fields.title.trim()} · 用户上传文件`);
      body.append("language", "auto");
      const payload = await api("/pldr-api/v1/intake/files?defer_candidates=true", { method: "POST", body });
      const items = starterItems(payload);
      importedCount += items.length;
      await linkStarterItems(investigation, items, "topic_onboarding_file", errors);
      items.filter((item) => item.status === "failed").forEach((item) => errors.push(`${file.name} 处理失败：${item.error || "未取得有效正文"}`));
    } catch (error) {
      errors.push(`${file.name} 未保存：${error.message || "未知错误"}`);
    }
  }

  if (fields.starter_urls.length) messages.push(`已保存 ${importedCount} 条网页、RSS、文本或文件资料`);
  else if (importedCount) messages.push(`已保存 ${importedCount} 条文本或文件资料`);
  if (!fields.starter_urls.length && !fields.starter_text && !fields.starter_files.length) messages.push("已用专题名称或关键词启动公开资料搜索");
  return { messages, errors };
}

function openInvestigationCreateModal() {
  const modal = $("#investigation-create-modal");
  const form = $("#investigation-create-form");
  form.reset();
  $("#investigation-create-question").dataset.autoSuggested = "true";
  $("#investigation-create-keyword").dataset.autoSeeded = "true";
  $("#investigation-create-question-note").textContent = "填写专题名称后，系统会建议一个核心问题。";
  $("#investigation-create-source-urls").setCustomValidity("");
  $("#investigation-create-text").setCustomValidity("");
  const advanced = $("#investigation-create-form .onboarding-advanced");
  if (advanced) advanced.open = false;
  syncInvestigationModeFields();
  $("#investigation-create-result").className = "import-result";
  $("#investigation-create-result").textContent = state.investigationMode === "unavailable" ? "专题服务不可用；提交后将创建明确标注的浏览器本地草稿。" : "";
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  $("#investigation-create-name").focus();
}

function closeInvestigationCreateModal() {
  const modal = $("#investigation-create-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
  state.investigationCreateRequestSerial += 1;
  const button = $("#investigation-create-submit");
  button.disabled = false;
  button.textContent = "创建专题并开始";
}

async function submitInvestigationCreate(event) {
  event.preventDefault();
  if (!validateInvestigationCreate()) return;
  const requestSerial = ++state.investigationCreateRequestSerial;
  const button = $("#investigation-create-submit");
  button.disabled = true;
  button.textContent = "正在创建专题…";
  setInvestigationCreateProgress("正在创建专题…");
  let investigation = null;
  try {
    const fields = investigationCreateFields();
    investigation = await createInvestigation(fields);
    if (requestSerial !== state.investigationCreateRequestSerial || !$("#investigation-create-modal").open) {
      renderInvestigationHome();
      toast("专题已创建；你已切换页面，所以没有自动跳转。", "info", 6500);
      return;
    }
    const outcome = await startInitialTopicCollection(investigation, fields);
    await refreshInvestigationDirectory().catch(() => null);
    if (requestSerial !== state.investigationCreateRequestSerial || !$("#investigation-create-modal").open) return;
    closeInvestigationCreateModal();
    renderInvestigationHome();
    await openInvestigation(investigation.id, "outcomes");
    const summary = [...outcome.messages, ...outcome.errors].join("；");
    toast(summary || "专题已创建。", outcome.errors.length ? "error" : investigation.sync_mode === "local" ? "info" : "success", outcome.errors.length ? 9000 : 7000);
  } catch (error) {
    if (requestSerial !== state.investigationCreateRequestSerial) {
      toast(`${investigation ? "专题已创建，但后续处理失败" : "后台创建专题失败"}：${error.message || "未知错误"}`, "error", 8000);
      return;
    }
    $("#investigation-create-result").className = `import-result ${investigation ? "success" : "error"}`;
    $("#investigation-create-result").textContent = investigation
      ? `专题已创建，但页面刷新失败：${error.message}。可关闭窗口后在“我的专题”中打开。`
      : `创建失败：${error.message}。未显示虚假成功。`;
  } finally {
    if (requestSerial === state.investigationCreateRequestSerial) {
      button.disabled = false;
      button.textContent = "创建专题并开始";
    }
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
    if (result.url) window.location.assign(result.url);
  } catch (error) {
    toast(`专题报告生成失败：${error.message}`, "error", 7000);
  } finally {
    setBusy(false);
  }
}

async function reorganizeInvestigation() {
  const investigation = activeInvestigation();
  if (!isServerInvestigation(investigation)) return;
  let preview;
  setBusy(true, "正在归并资料并提炼关键发现");
  try {
    preview = await api(API_ROUTES.investigationReorganization(investigation.id, "preview"), { method: "POST" });
  } catch (error) {
    toast(`专题重新整理失败：${error.message || "大模型暂时不可用"}`, "error", 9000);
    return;
  } finally {
    setBusy(false);
  }
  const groupLines = (preview.groups || []).slice(0, 10).map((group, index) => (
    `${index + 1}. ${group.title}（${group.source_event_ids?.length || 0} 份资料）\n${(group.findings || []).slice(0, 2).map((finding) => `   · ${finding.text}`).join("\n")}`
  ));
  const accepted = window.confirm([
    `系统建议把 ${preview.source_event_count} 个现有条目整理为 ${preview.proposed_event_count} 个真实事件：`,
    "",
    `专题结论：${preview.current_answer || "暂时无法形成总体结论"}`,
    "",
    ...groupLines,
    "",
    "确认后，专题成果和新报告将按这些事件展示；原始资料和旧记录仍会保留。是否采用？",
  ].join("\n"));
  if (!accepted) {
    toast("已保留原专题内容，没有进行修改。", "info");
    return;
  }
  setBusy(true, "正在保存重新整理后的专题");
  try {
    const result = await api(API_ROUTES.investigationReorganization(investigation.id, "confirm"), {
      method: "POST",
      body: JSON.stringify({ draft_id: preview.draft_id, actor: "analyst" }),
    });
    await refreshInvestigationDirectory();
    await loadInvestigationWorkspace(investigation.id, { quiet: true });
    toast(`重新整理完成：${result.source_event_count || preview.source_event_count} 个条目归并为 ${result.event_count || preview.proposed_event_count} 个真实事件。`, "success", 8000);
  } catch (error) {
    toast(`保存重新整理结果失败：${error.message || "未知错误"}`, "error", 9000);
  } finally {
    setBusy(false);
  }
}

async function handleInvestigationAction(action, node) {
  const investigation = activeInvestigation();
  if (action === "create") return openInvestigationCreateModal();
  if (action === "restore-topic") {
    const targetId = node?.dataset?.investigationTarget;
    if (!targetId) return;
    try {
      await api(API_ROUTES.investigation(targetId), { method: "PATCH", body: JSON.stringify({ status: "active", actor: "analyst" }) });
      await refreshInvestigationDirectory();
      toast("专题已恢复。", "success");
    } catch (error) {
      toast(`恢复专题失败：${error.message || "未知错误"}`, "error", 7000);
    }
    return;
  }
  if (action === "archive-topic") {
    if (!isServerInvestigation(investigation) || investigation.sync_mode === "system") return;
    if (!window.confirm(`删除专题“${investigation.title}”？\n\n专题会移到“已删除专题”，以后可以恢复；已采集资料和正式档案不会被物理删除。`)) return;
    try {
      const payload = await api(API_ROUTES.investigation(investigation.id), { method: "PATCH", body: JSON.stringify({ status: "archived", actor: "analyst" }) });
      const archived = normalizeInvestigation(payload.investigation || payload, "server");
      state.investigations = state.investigations.map((item) => item.id === investigation.id
        ? { ...item, ...archived, status: "archived" }
        : item);
      state.investigationTasks.delete(investigation.id);
      state.investigationTaskErrors.delete(investigation.id);
      state.investigationDetails.delete(investigation.id);
      state.investigationLinks.delete(investigation.id);
      state.investigationActivities.delete(investigation.id);
      state.investigationActivityErrors.delete(investigation.id);
      state.investigationOutcomes.delete(investigation.id);
      state.investigationOutcomeErrors.delete(investigation.id);
      showInvestigationHome();
      await refreshInvestigationDirectory();
      toast("专题已移到“已删除专题”。", "success", 5200);
    } catch (error) {
      toast(`删除专题失败：${error.message || "未知错误"}`, "error", 7000);
    }
    return;
  }
  if (action === "search") return openExternalSearchModal(investigation?.id);
  if (action === "open-discovery") return setInvestigationTab("materials", { section: "discovery" });
  if (action === "open-search-run") {
    await openExternalSearchModal(investigation?.id);
    return openSearchHistoryRun(node?.dataset?.searchRunId);
  }
  if (action === "reorganize") return reorganizeInvestigation();
  if (action === "import") return openImportModal(investigation?.id);
  if (action === "review") return openIntakeModal(null, false, isServerInvestigation(investigation) ? investigation.id : null);
  if (action === "claims") return setInvestigationTab("outcomes", { section: "claims" });
  if (action === "monitoring") return setInvestigationTab("materials", { section: "monitoring" });
  if (action === "classic") return showClassicWorkspace({ scopeInvestigationId: investigation?.id || null });
  if (action === "open-intake") return openIntakeModal(null, false, isServerInvestigation(investigation) ? investigation.id : null);
  if (action === "refresh") {
    await refreshData({ keepSelection: true, quiet: true });
    await refreshInvestigationDirectory();
    if (investigation) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    const refreshedInvestigation = activeInvestigation();
    if (refreshedInvestigation && investigationNeedsEvidence()) {
      await loadInvestigationEvidence(refreshedInvestigation, { force: true });
    }
    toast("专题数据已刷新。", "success");
    return;
  }
  if (action === "add-source") {
    state.pendingCollectionInvestigationId = investigation?.id || null;
    return openCollectionModal(
      null,
      isServerInvestigation(investigation) ? investigation.id : null,
    );
  }
  if (action === "open-source") {
    state.pendingCollectionInvestigationId = investigation?.id || null;
    return openCollectionModal(
      node.dataset.targetId,
      isServerInvestigation(investigation) ? investigation.id : null,
    );
  }
  if (["open-review", "accept-entry", "reject-entry"].includes(action)) {
    const intakeId = node.dataset.intakeId;
    await openIntakeModal(intakeId, false, isServerInvestigation(investigation) ? investigation.id : null);
    if (action === "accept-entry") toast("请查看原文和整理结果，再选择加入专题、修改或忽略。", "info", 6000);
    if (action === "reject-entry") toast("请在处理页展开“忽略”，填写原因后确认。", "info", 6000);
    return;
  }
  if (action === "remove-task") {
    const taskId = node.dataset.taskId;
    const intakeId = node.dataset.intakeId;
    const task = tasksForInvestigation(investigation).find((item) => (
      (!taskId || String(item.id || item.task_id || "") === taskId)
      && taskIntakeId(item) === intakeId
    ));
    if (
      !isServerInvestigation(investigation)
      || !intakeId
      || canonicalTaskStage(task) !== "failed"
      || !recordAllowsAction(task, "remove_from_investigation", "remove")
    ) {
      toast("当前任务不允许从专题移除，请刷新后重试。", "error", 6000);
      return;
    }
    if (!window.confirm(`从“${investigation.title}”移除“${taskTitle(task)}”？\n\n它会移到本专题“已删除”，之后可以恢复；全局材料和正式档案不会改变。`)) return;
    try {
      await api(API_ROUTES.investigationIntakeAction(investigation.id, intakeId, "remove"), { method: "POST" });
      toast("材料已从本专题移除；可在“已删除”中恢复。", "success", 5600);
      await refreshInvestigationDirectory();
      if (state.activeInvestigationId === investigation.id) {
        await loadInvestigationWorkspace(investigation.id, { quiet: true });
      }
    } catch (error) {
      toast(`从专题移除失败：${error.message || "未知错误"}`, "error", 7000);
    }
    return;
  }
  if (action === "retry-task") return retryInvestigationTask(node.dataset.taskId);
  if (action === "retry-intake") {
    const intakeId = node.dataset.intakeId;
    if (!intakeId) {
      toast("任务缺少材料编号，无法重新生成候选。", "error", 7000);
      return;
    }
    try {
      await api(`/pldr-api/v1/intake/${encodeURIComponent(intakeId)}/regenerate`, { method: "POST" });
      toast("候选已重新生成。", "success");
      if (investigation) await loadInvestigationWorkspace(investigation.id, { quiet: true });
    } catch (error) {
      toast(`候选重新生成失败：${error.message || "未知错误"}`, "error", 7000);
    }
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

function clearEventOverviewSelection({ closeOpenDrawer = false, clearUrl = false } = {}) {
  state.selectedEventRequestSerial += 1;
  state.selectedId = null;
  state.selectedEvent = null;
  state.selectedEventError = null;
  $("#btn-report").disabled = true;
  if (clearUrl) {
    const url = new URL(window.location.href);
    url.searchParams.delete("event");
    history.replaceState(null, "", url);
  }
  if (closeOpenDrawer && $("#event-drawer")?.classList.contains("open")) closeDrawer();
}

function resetEventOverviewContext() {
  clearEventOverviewSelection({ closeOpenDrawer: true });
  $("#search").value = "";
  $("#importance-filter").value = "";
  $("#language-filter").value = "";
  $("#contested-filter").checked = false;
  state.filteredEvents = [];
}

function applyFilters() {
  const query = $("#search").value.trim().toLocaleLowerCase();
  const importance = $("#importance-filter").value;
  const language = $("#language-filter").value;
  const contestedOnly = $("#contested-filter").checked;

  state.filteredEvents = eventOverviewEvents().filter((event) => {
    if (query && !eventSearchText(event).includes(query)) return false;
    if (importance && event.importance !== importance) return false;
    if (language && !(event.languages || []).includes(language)) return false;
    if (contestedOnly && !event.has_contested_claim) return false;
    return true;
  });

  const selectionBecameHidden = Boolean(state.selectedId)
    && !state.filteredEvents.some((event) => event.id === state.selectedId);
  if (selectionBecameHidden) {
    clearEventOverviewSelection({ closeOpenDrawer: true, clearUrl: true });
  }

  renderEvents();
  renderMap();
  renderTimeline();
  if (selectionBecameHidden) {
    renderAssessment();
    renderGaps();
  }
}

function renderTopic() {
  const topic = state.overview?.topic || {};
  const scopeInvestigation = eventOverviewInvestigation();
  if (eventOverviewScopeUnavailable()) {
    $("#topic-title").textContent = "专题范围不可用";
    $("#topic-description").textContent = "这个专题可能已删除、权限已变化或目录读取失败。为避免混入其他专题，当前保持空白。";
    $("#topic-mode").textContent = "已失效关闭";
    $("#topic-range").textContent = "未显示任何事件";
    $("#topic-updated").textContent = "请返回我的专题";
    $("#map-title").textContent = "专题范围不可用";
    renderEventOverviewScope();
    return;
  }
  $("#topic-title").textContent = scopeInvestigation ? `${scopeInvestigation.title} · 事件总览` : topic.title || "事件总览";
  $("#topic-description").textContent = scopeInvestigation ? scopeInvestigation.question : topic.description || topic.subtitle || "";
  if (scopeInvestigation) {
    const scopedEvents = eventOverviewEvents();
    const eventDates = scopedEvents.map((event) => event.start_at).filter(Boolean).sort((a, b) => new Date(a) - new Date(b));
    $("#topic-mode").textContent = "仅本专题正式事件";
    $("#topic-range").textContent = eventDates.length ? `${formatDate(eventDates[0])} 至 ${formatDate(eventDates[eventDates.length - 1])}` : "暂无已确认事件";
    $("#topic-updated").textContent = `专题更新 ${formatDate(scopeInvestigation.updated_at, true)}`;
    $("#map-title").textContent = "本专题事件态势";
  } else {
    $("#topic-mode").textContent = LABELS.mode[topic.mode] || topic.mode || "专题模式";
    $("#topic-range").textContent = `${formatDate(topic.time_range?.start)} 至 ${formatDate(topic.time_range?.end)}`;
    $("#topic-updated").textContent = `更新 ${formatDate(state.overview?.last_updated, true)}`;
    $("#map-title").textContent = "全局事件态势";
  }
  renderEventOverviewScope();
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
    const scopeInvestigation = eventOverviewInvestigation();
    if (eventOverviewScopeUnavailable()) {
      items = [
        ["events", 0, "专题事件"],
        ["documents", 0, "关联文档"],
        ["contested", 0, "待核实主张"],
        ["sources", 0, "专题来源"],
      ];
      $("#metrics").setAttribute("aria-label", "专题范围不可用，未显示全局指标");
    } else if (scopeInvestigation) {
      const scopedEvents = eventOverviewEvents();
      const documents = scopedEvents.reduce((sum, event) => sum + Number(event.document_count || 0), 0);
      const contested = scopedEvents.reduce((sum, event) => sum + Number(event.claim_counts?.contested || 0) + Number(event.claim_counts?.unverified || 0), 0);
      items = [
        ["events", scopedEvents.length, "专题事件"],
        ["documents", documents, "关联文档"],
        ["contested", contested, "待核实主张"],
        ["sources", investigationMetrics(scopeInvestigation).sources, "专题来源"],
      ];
      $("#metrics").setAttribute("aria-label", `${scopeInvestigation.title} 事件总览指标`);
    } else {
      items = [
        ["events", overviewMetrics.events ?? 0, "全局事件"],
        ["documents", overviewMetrics.documents ?? 0, "全局文档"],
        ["independence", overviewMetrics.independence_groups ?? 0, "独立源组"],
        ["contested", overviewMetrics.contested_claims ?? 0, "争议主张"],
        ["intake", intake.candidate_ready ?? 0, "全局待审"],
        ["collection", changed, "监测待审"],
      ];
      $("#metrics").setAttribute("aria-label", "事件总览全局指标");
    }
  } else if (investigation) {
    const metrics = investigationMetrics(investigation);
    const outcomeCounts = outcomeForInvestigation(investigation)?.counts || {};
    const topicTasks = tasksForInvestigation(investigation).filter(taskIsActive);
    const needsAttention = topicTasks.filter((task) => ["ready", "failed"].includes(canonicalTaskStage(task))).length;
    const processing = topicTasks.filter((task) => ["queued", "fetching", "generating"].includes(canonicalTaskStage(task))).length;
    items = [
      ["queue", needsAttention, "待处理"],
      ["review", processing, "系统处理中"],
      ["events", outcomeCounts.events ?? metrics.events, "已确认事件"],
      ["sources", outcomeCounts.sources ?? metrics.sources, "资料来源"],
    ];
    $("#metrics").setAttribute("aria-label", `${investigation.title} 专题指标`);
  } else {
    const userInvestigations = state.investigations.filter((item) => !["system", "demo", "compatibility"].includes(item.sync_mode) && item.status !== "archived");
    const assignments = allHomeAssignments();
    items = [
      ["investigations", userInvestigations.length, "我的专题"],
      ["queue", assignments.length, "待处理"],
      ["review", assignments.filter(({ task }) => canonicalTaskStage(task) === "ready").length, "待确认"],
      ["failed", assignments.filter(({ task }) => canonicalTaskStage(task) === "failed").length, "失败待恢复"],
    ];
    $("#metrics").setAttribute("aria-label", "我的专题指标");
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
    const hasEventsInScope = eventOverviewEvents().length > 0;
    root.innerHTML = `
      <div class="list-empty">
        <span>⌕</span>
        <p>${hasEventsInScope ? "当前筛选条件下没有事件。" : "当前范围还没有已确认事件。"}</p>
        ${hasEventsInScope ? '<button type="button" class="text-btn" data-action="clear-filters">清除筛选</button>' : ""}
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
  $("#markers").innerHTML = eventOverviewEvents().map((event) => {
    const position = mapPosition(event);
    if (!position) return "";
    const muted = !visibleIds.has(event.id);
    const active = state.selectedId === event.id;
    return `
      <button
        type="button"
        class="map-marker ${escapeHtml(event.importance)} ${active ? "active" : ""} ${muted ? "muted" : ""}"
        style="left:${position.left}%;top:${position.top}%"
        ${muted ? "disabled aria-hidden=\"true\"" : `data-event-id="${escapeHtml(event.id)}"`}
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
  const scopeInvestigation = eventOverviewInvestigation();
  if (eventOverviewScopeUnavailable()) {
    $("#source-title").textContent = "专题来源不可用";
    $("#source-summary").textContent = "0 / 0";
    $("#sources").innerHTML = '<div class="panel-empty">专题范围已失效；没有回退显示全局来源。</div>';
    return;
  }
  if (scopeInvestigation) {
    const targets = targetsForInvestigation(scopeInvestigation);
    const targetRows = targets.map((target) => {
      const actualStatus = collectionTargetStatus(target);
      const tone = actualStatus === "healthy" ? "healthy" : ["error", "degraded"].includes(actualStatus) ? "error" : actualStatus === "paused" ? "disabled" : "stale";
      return { target, actualStatus, tone };
    });
    const counts = {
      healthy: targetRows.filter((item) => item.tone === "healthy").length,
      attention: targetRows.filter((item) => ["stale", "disabled"].includes(item.tone)).length,
      error: targetRows.filter((item) => item.tone === "error").length,
    };
    $("#source-title").textContent = "专题来源状态";
    $("#source-summary").textContent = `${counts.healthy} / ${targets.length}`;
    $("#sources").innerHTML = targets.length ? `
      <div class="source-overview">
        <div><strong>${counts.healthy}</strong><span>正常</span></div>
        <div><strong>${counts.attention}</strong><span>需检查</span></div>
        <div><strong>${counts.error}</strong><span>异常</span></div>
      </div>
      <div class="source-scroll">
        ${targetRows.slice(0, 12).map(({ target, actualStatus, tone }) => `
          <div class="source-row">
            <span class="source-status ${escapeHtml(tone)}"></span>
            <div>
              <strong>${escapeHtml(target.name || target.title || "未命名监测来源")}</strong>
              <small>${escapeHtml(target.url || target.canonical_url || "地址未知")}${collectionIntervalMinutes(target) ? ` · ${escapeHtml(collectionIntervalMinutes(target))} 分钟` : ""}</small>
            </div>
            <span class="source-label ${escapeHtml(tone)}">${escapeHtml(LABELS.collectionStatus[actualStatus] || "需检查")}</span>
          </div>`).join("")}
      </div>` : '<div class="panel-empty">当前专题没有关联监测来源；这里不会混入全局来源状态。</div>';
    return;
  }
  $("#source-title").textContent = "来源状态";
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
  if (eventOverviewScopeUnavailable()) return [];
  const scopeInvestigation = eventOverviewInvestigation();
  const selectedBelongsToScope = state.filteredEvents.some((event) => event.id === state.selectedEvent?.id);
  const eventGaps = selectedBelongsToScope ? state.selectedEvent?.assessment?.information_gaps || [] : [];
  if (scopeInvestigation) return [...new Set(eventGaps)].slice(0, 10);
  const topicGaps = state.overview?.information_gaps || [];
  return [...new Set([...eventGaps, ...topicGaps])].slice(0, 10);
}

function renderGaps() {
  const gaps = currentGaps();
  const unavailableScope = eventOverviewScopeUnavailable();
  const scoped = Boolean(state.eventOverviewScopeInvestigationId);
  $("#gap-title").textContent = scoped ? "专题信息缺口" : "信息缺口";
  $("#gap-count").textContent = String(gaps.length);
  $("#gaps").innerHTML = gaps.length
    ? gaps.map((gap, index) => `
        <div class="gap-row">
          <span>${String(index + 1).padStart(2, "0")}</span>
          <p>${escapeHtml(gap)}</p>
        </div>`).join("")
    : `<div class="panel-empty">${unavailableScope ? "专题范围已失效；没有回退显示全局信息缺口。" : scoped ? "当前专题所选事件没有登记的信息缺口；这里不会混入全局缺口。" : "当前没有登记的信息缺口。"}</div>`;
}

function renderAssessment() {
  const root = $("#assessment");
  const scopedEvents = state.filteredEvents;
  const selectedIdBelongsToScope = Boolean(state.selectedId)
    && scopedEvents.some((event) => event.id === state.selectedId);
  const selectedBelongsToScope = selectedIdBelongsToScope
    && state.selectedEvent?.id === state.selectedId;
  const assessment = selectedBelongsToScope ? state.selectedEvent?.assessment : null;
  if (!assessment) {
    if (state.selectedEventError && selectedIdBelongsToScope) {
      $("#assessment-confidence").textContent = "读取失败";
      root.className = "panel-body";
      root.innerHTML = renderOperationalError(state.selectedEventError, {
        stage: "fetch",
        actionHtml: '<button class="btn btn-ghost" type="button" data-retry-selected-event>重新读取事件档案</button>',
      });
      return;
    }
    $("#assessment-confidence").textContent = selectedIdBelongsToScope ? "正在读取" : "未选择事件";
    root.className = "panel-body empty-state";
    if (selectedIdBelongsToScope) {
      root.innerHTML = '<div class="empty-icon">◎</div><p>正在读取所选事件的研判与信息缺口…</p>';
    } else if (!state.filteredEvents.length && eventOverviewEvents().length) {
      root.innerHTML = '<div class="empty-icon">⌕</div><p>当前筛选没有匹配事件。清除或调整筛选后再查看研判。</p>';
    } else if (!eventOverviewEvents().length) {
      root.innerHTML = '<div class="empty-icon">◎</div><p>当前范围还没有已确认事件可供研判。</p>';
    } else {
      root.innerHTML = '<div class="empty-icon">◎</div><p>选择一个可见事件，查看当前判断、关键假设、替代解释与证伪条件。</p>';
    }
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
    const card = state.events.find((item) => item.id === state.selectedId);
    $("#drawer-title").textContent = card?.title || "事件档案";
    $("#drawer-summary").innerHTML = `<p>${state.selectedEventError ? "事件详情读取失败，未显示上一个事件的数据。" : "正在读取这个事件的正式档案…"}</p>`;
    root.innerHTML = state.selectedEventError
      ? renderOperationalError(state.selectedEventError, {
        stage: "fetch",
        actionHtml: '<button class="btn btn-ghost" type="button" data-retry-selected-event>重新读取事件档案</button>',
      })
      : '<div class="drawer-loading">正在加载事件档案…</div>';
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
              <a href="${escapeHtml(withEventContext(evidence.snapshot_url || evidence.document.snapshot_url, event.id))}">查看证据快照</a>
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
        const similar = document.metadata?.similar_to_document_id;
        return `
          <article class="document-card">
            <span class="document-index">${String(index + 1).padStart(2, "0")}</span>
            <div>
              <div class="document-meta">
                <span>${escapeHtml(document.source.name)}</span>
                <span>T${document.source.reliability_tier}</span>
                <span>${escapeHtml(document.language)}</span>
                ${duplicate ? '<span class="duplicate-chip">重复来源</span>' : similar ? '<span class="duplicate-chip">相似报道</span>' : ""}
              </div>
                <h3>${escapeHtml(document.title || "未知标题")}</h3>
              <p>${formatDate(document.published_at, true)} · 抓取 ${formatDate(document.fetched_at, true)}</p>
              <small>来源与原文快照已保存</small>
            </div>
            <div class="document-actions">
              <a href="${escapeHtml(withEventContext(document.snapshot_url, event.id))}">证据快照</a>
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
  const scopeId = state.eventOverviewScopeInvestigationId;
  if (scopeId && !eventOverviewEvents().some((event) => event.id === eventId)) return;
  if (!state.filteredEvents.some((event) => event.id === eventId)) return;
  const requestSerial = ++state.selectedEventRequestSerial;
  state.selectedId = eventId;
  state.selectedEvent = null;
  state.selectedEventError = null;
  renderEvents();
  renderMap();
  renderTimeline();
  renderAssessment();
  renderGaps();
  $("#btn-report").disabled = false;

  if (open) {
    openDrawer(tab);
  } else if ($("#event-drawer")?.classList.contains("open")) {
    renderDrawer();
  }

  try {
    const event = await api(`/pldr-api/v1/events/${encodeURIComponent(eventId)}`);
    if (
      requestSerial !== state.selectedEventRequestSerial
      || state.selectedId !== eventId
      || state.eventOverviewScopeInvestigationId !== scopeId
      || !state.filteredEvents.some((item) => item.id === eventId)
    ) return;
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
    if (
      requestSerial !== state.selectedEventRequestSerial
      || state.selectedId !== eventId
      || state.eventOverviewScopeInvestigationId !== scopeId
    ) return;
    state.selectedEvent = null;
    state.selectedEventError = error;
    renderAssessment();
    renderGaps();
    if (open || $("#event-drawer").classList.contains("open")) renderDrawer();
    toast(`事件加载失败：${error.message}`, "error");
  }
}

async function generateReport(eventIds = null) {
  const ids = eventIds || (state.selectedId ? [state.selectedId] : []);
  const scopeInvestigation = eventOverviewInvestigation();
  const scopeInvestigationId = isServerInvestigation(scopeInvestigation)
    ? scopeInvestigation.id
    : null;
  const selectedIsVisible = ids.length === 1
    && state.filteredEvents.some((event) => event.id === ids[0]);
  if (!selectedIsVisible) {
    toast("请先从当前可见结果中选择一个事件。", "error");
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
        ...(scopeInvestigationId ? { investigation_id: scopeInvestigationId } : {}),
      }),
    });
    toast(`简报已生成，共 ${result.evidence_count} 条证据。`, "success");
    window.location.assign(result.url);
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
  state.importRequestSerial += 1;
  const submit = $("#import-submit");
  submit.disabled = false;
  submit.textContent = "提交到采集箱";
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

async function openExternalSearchModal(preferredInvestigationId = state.activeInvestigationId, { keyword = "", autoStart = false } = {}) {
  const modal = $("#search-modal");
  state.searchHistoryVisibility = "active";
  renderDestinationPickers(preferredInvestigationId);
  if (typeof modal.showModal === "function" && !modal.open) modal.showModal();
  else if (!modal.open) modal.setAttribute("open", "");
  renderSearchProvider();
  const destinationId = currentSearchDestinationId();
  switchSearchWorkspaceContext(destinationId);
  const openSerial = state.searchRequestSerial;
  renderSearchHistory();
  renderSearchResults();
  updateSearchSelectionCount();
  await loadSearchHistory(destinationId);
  if (openSerial !== state.searchRequestSerial || !modal.open) return;
  if (keyword) $("#search-keyword").value = keyword;
  $("#search-keyword").focus();
  if (keyword && autoStart) $("#search-form").requestSubmit();
}

function closeExternalSearchModal() {
  const modal = $("#search-modal");
  const runId = currentSearchRunId();
  if (runId) state.searchSelectionsByRun.set(runId, new Set(state.searchSelectedIds));
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
  state.searchRequestSerial += 1;
  state.searchBusy = false;
  $("#search-destination").disabled = false;
  $("#search-submit").disabled = false;
  $("#search-submit").textContent = "开始搜索";
  $("#search-select").textContent = "加入专题并开始处理";
}

function detectSearchLanguage(keyword) {
  if (/\p{Script=Han}/u.test(keyword)) return "zh";
  if (/\p{Script=Arabic}/u.test(keyword)) return "ar";
  return "en";
}

function selectedSearchLanguage(keyword = $("#search-keyword")?.value.trim() || "") {
  return detectSearchLanguage(keyword);
}

function renderSearchProvider() {
  const search = state.config?.external_search || {};
  const summary = $("#search-provider-summary");
  if (!summary) return;
  summary.className = `search-provider-summary ${search.configured ? "ok" : "warning"}`;
  summary.innerHTML = `
    <details>
      <summary><strong>${escapeHtml(search.component || search.provider || "外部检索后端")}</strong><span>${search.configured ? "已连接" : "未配置"}</span></summary>
      <div class="search-provider-detail">
        <span>${escapeHtml(search.version || "版本未知")} · ${escapeHtml(search.license || "许可证未知")}</span>
        <small>${escapeHtml(search.deployment_boundary || "部署边界未知")}</small>
        ${search.configured ? "" : `<em>${escapeHtml(search.error || "尚未配置；不会用演示数据伪装结果。")}</em>`}
      </div>
    </details>`;
}

function currentSearchRunId(payload = state.searchRun) {
  return payload?.query_run_id || payload?.run?.query_run_id || payload?.id || payload?.run?.id || null;
}

function currentSearchDestinationId() {
  const raw = $("#search-destination")?.value;
  return raw === UNASSIGNED_VALUE ? unclassifiedInvestigation()?.id : raw || null;
}

function switchSearchWorkspaceContext(destinationId) {
  if (destinationId === state.searchHistoryInvestigationId) return;
  const previousContext = state.searchHistoryInvestigationId || state.searchRun?.investigation_id || null;
  if (previousContext && previousContext !== destinationId) {
    const runId = currentSearchRunId();
    if (runId) state.searchSelectionsByRun.set(runId, new Set(state.searchSelectedIds));
    state.searchRun = null;
    state.searchResults = [];
    state.searchCurrentPageIds = [];
    state.searchSelectedIds = new Set();
    state.searchPage = 1;
    state.searchHasMore = false;
    state.searchNextPage = null;
    state.searchNextCursor = null;
    state.searchLastRequest = null;
    state.searchError = "";
    state.searchErrorInfo = null;
    state.searchErrorSource = null;
    const status = $("#search-status");
    if (status) {
      status.className = "search-status";
      status.textContent = "输入关键词后开始检索；每次默认加载 20 条。";
    }
  }
  state.searchHistory = [];
  state.searchHistoryError = "";
  state.searchHistoryInvestigationId = destinationId || null;
  state.searchHistoryRequestSerial += 1;
  state.searchRequestSerial += 1;
  state.searchHistoryBusy = false;
  state.searchBusy = false;
  if ($("#search-destination")) $("#search-destination").disabled = false;
  const submit = $("#search-submit");
  if (submit) {
    submit.disabled = false;
    submit.textContent = "开始搜索";
  }
  renderSearchResultSummary();
  updateSearchSelectionCount();
}

function beginSearchRequest() {
  return {
    serial: ++state.searchRequestSerial,
    destinationId: currentSearchDestinationId(),
  };
}

function searchRequestIsCurrent(requestContext) {
  return requestContext.serial === state.searchRequestSerial
    && requestContext.destinationId === currentSearchDestinationId();
}

function normalizeSearchRun(raw) {
  const run = raw?.run && typeof raw.run === "object" ? { ...raw, ...raw.run } : raw || {};
  return {
    ...run,
    id: run.query_run_id || run.id,
    query_run_id: run.query_run_id || run.id,
    keyword: run.keyword || run.query || "未知关键词",
    scope: run.scope || "web",
    language: run.language || "",
    created_at: run.created_at || run.started_at || run.executed_at || run.updated_at,
    returned_count: run.returned_count ?? run.loaded_count ?? run.results?.length ?? run.items?.length ?? run.result_count ?? 0,
  };
}

function upsertSearchHistory(raw) {
  const run = normalizeSearchRun(raw);
  if (!run.id) return;
  const archived = recordIsArchived(run);
  if ((state.searchHistoryVisibility === "archived") !== archived) return;
  state.searchHistory = [run, ...state.searchHistory.filter((item) => item.id !== run.id)]
    .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
    .slice(0, 20);
  renderSearchHistory();
}

function searchRunAllowsAction(run, action) {
  const allowed = run?.allowed_actions;
  if (Array.isArray(allowed)) return allowed.includes(action);
  if (allowed && typeof allowed === "object" && Object.prototype.hasOwnProperty.call(allowed, action)) {
    return allowed[action] === true || allowed[action]?.allowed === true;
  }
  const archived = recordIsArchived(run) || state.searchHistoryVisibility === "archived";
  if (action === "restore") return archived;
  return action === "archive" && !archived;
}

function renderSearchHistory() {
  const root = $("#search-history");
  if (!root) return;
  if (state.searchHistoryBusy && !state.searchHistory.length) {
    root.innerHTML = '<p class="search-history-empty">正在读取查询记录…</p>';
    return;
  }
  const currentId = currentSearchRunId();
  $$('[data-search-history-visibility]').forEach((button) => button.classList.toggle("active", button.dataset.searchHistoryVisibility === state.searchHistoryVisibility));
  const errorMarkup = state.searchHistoryError
    ? `<div class="search-history-error" role="alert"><span>查询记录读取失败：${escapeHtml(state.searchHistoryError)}</span><button class="text-btn" type="button" data-search-history-retry>重试</button></div>`
    : "";
  const historyMarkup = state.searchHistory.length ? state.searchHistory.map((raw) => {
    const run = normalizeSearchRun(raw);
    const archived = recordIsArchived(run) || state.searchHistoryVisibility === "archived";
    const action = archived ? "restore" : "archive";
    const actionButton = searchRunAllowsAction(run, action)
      ? `<button class="text-btn search-history-record-action" type="button" data-search-run-action="${action}" data-search-run-id="${escapeHtml(run.id)}">${archived ? "恢复" : "删除本次查询"}</button>`
      : "";
    return `
      <article class="search-history-record">
        <button class="search-history-item ${run.id === currentId ? "active" : ""}" type="button" data-search-history-run="${escapeHtml(run.id)}">
          <span class="search-history-topline"><strong>${escapeHtml(run.keyword)}</strong><time>${formatDate(run.created_at, true)}</time></span>
          <span>${escapeHtml(LABELS.searchScope[run.scope] || run.scope)}</span>
          <small>${archived ? "已删除" : `已加载 ${Number(run.loaded_count ?? run.returned_count ?? 0)} 条${run.status === "failed" || run.error ? " · 查询失败" : ""}`}</small>
        </button>
        ${actionButton}
      </article>`;
  }).join("") : `<p class="search-history-empty">${state.searchHistoryVisibility === "archived" ? "没有可恢复的已删除查询。" : state.searchHistoryAvailable ? "还没有查询记录。完成一次搜索后可从这里重新打开。" : "当前后端未提供历史接口；本次页面中的查询仍会保留。"}</p>`;
  root.innerHTML = errorMarkup + historyMarkup;
}

async function loadSearchHistory(investigationId = state.activeInvestigationId) {
  if (!state.searchHistoryAvailable) return;
  const rawDestinationId = investigationId || $("#search-destination")?.value;
  const destinationId = rawDestinationId === UNASSIGNED_VALUE ? unclassifiedInvestigation()?.id : rawDestinationId;
  const destination = state.investigations.find((item) => item.id === destinationId);
  if (!destinationId || destinationId === NEW_INVESTIGATION_VALUE || (destination && !isServerInvestigation(destination))) return;
  switchSearchWorkspaceContext(destinationId);
  const requestSerial = ++state.searchHistoryRequestSerial;
  state.searchHistoryBusy = true;
  state.searchHistoryError = "";
  renderSearchHistory();
  try {
    const params = new URLSearchParams({ limit: "12", visibility: state.searchHistoryVisibility });
    params.set("investigation_id", destinationId);
    const payload = await api(`${API_ROUTES.searchRuns}?${params}`);
    if (requestSerial !== state.searchHistoryRequestSerial || destinationId !== state.searchHistoryInvestigationId) return;
    const runs = unwrapItems(payload, "runs", "items").map(normalizeSearchRun);
    const current = currentSearchRunId()
      && state.searchRun?.investigation_id === destinationId
      && (state.searchHistoryVisibility === "archived") === recordIsArchived(state.searchRun)
      ? [normalizeSearchRun(state.searchRun)]
      : [];
    const byId = new Map([...current, ...runs].filter((run) => run.id).map((run) => [run.id, run]));
    state.searchHistory = [...byId.values()].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).slice(0, 20);
    state.searchHistoryError = "";
  } catch (error) {
    if (requestSerial !== state.searchHistoryRequestSerial) return;
    if (isUnsupportedEndpoint(error)) state.searchHistoryAvailable = false;
    else state.searchHistoryError = error.message || "未知错误";
  } finally {
    if (requestSerial === state.searchHistoryRequestSerial) {
      state.searchHistoryBusy = false;
      renderSearchHistory();
    }
  }
}

async function setSearchHistoryVisibility(visibility) {
  if (!new Set(["active", "archived"]).has(visibility) || state.searchHistoryVisibility === visibility) return;
  state.searchHistoryVisibility = visibility;
  state.searchHistory = [];
  renderSearchHistory();
  await loadSearchHistory(state.searchHistoryInvestigationId || currentSearchDestinationId());
}

async function updateSearchRunVisibility(runId, action) {
  const run = state.searchHistory.find((item) => item.id === runId) || (currentSearchRunId() === runId ? state.searchRun : null);
  if (!run) return;
  if (!searchRunAllowsAction(normalizeSearchRun(run), action)) {
    toast("当前查询状态不允许执行这项删除或恢复操作，请刷新后重试。", "error", 6000);
    return;
  }
  const restoring = action === "restore";
  const question = restoring
    ? `恢复查询“${normalizeSearchRun(run).keyword}”？\n\n恢复只会让查询重新出现在历史记录中。`
    : `删除查询“${normalizeSearchRun(run).keyword}”？\n\n只会隐藏这次查询记录，不会删除已经选择、抓取或保存的材料；之后可以恢复。`;
  if (!window.confirm(question)) return;
  try {
    const payload = await api(API_ROUTES.searchRunAction(runId, action), { method: "POST" });
    if (currentSearchRunId() === runId) {
      state.searchRun = {
        ...state.searchRun,
        ...(payload?.query_run || payload?.run || payload || {}),
        archived: !restoring,
        archived_at: restoring ? null : payload?.archived_at || new Date().toISOString(),
      };
    }
    state.searchHistory = state.searchHistory.filter((item) => item.id !== runId);
    renderSearchHistory();
    await loadSearchHistory(state.searchHistoryInvestigationId || currentSearchDestinationId());
    toast(restoring ? "查询记录已恢复。" : "查询记录已移到“已删除”；已选材料不受影响。", "success", 5200);
  } catch (error) {
    toast(`${restoring ? "恢复" : "删除"}查询失败：${error.message || "未知错误"}`, "error", 7000);
  }
}

function searchPayloadResults(payload) {
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.run?.results)) return payload.run.results;
  if (Array.isArray(payload?.run?.items)) return payload.run.items;
  return [];
}

function applySearchPayload(payload, { append = false } = {}) {
  const runIdBefore = currentSearchRunId();
  if (runIdBefore) state.searchSelectionsByRun.set(runIdBefore, new Set(state.searchSelectedIds));
  const combinedPayload = payload?.run && typeof payload.run === "object" ? { ...payload, ...payload.run } : payload || {};
  const pagination = combinedPayload.pagination || {};
  const page = Number(combinedPayload.page ?? pagination.page ?? (append ? state.searchPage + 1 : 1)) || 1;
  const incoming = searchPayloadResults(payload).map((result) => ({ ...result, _loadedPage: page }));
  const byId = new Map((append ? state.searchResults : []).map((result) => [result.id, result]));
  incoming.forEach((result) => byId.set(result.id, { ...byId.get(result.id), ...result }));
  state.searchResults = [...byId.values()];
  state.searchPage = page;
  state.searchPageSize = Number(combinedPayload.page_size ?? pagination.page_size ?? $("#search-page-size")?.value ?? 20) || 20;
  if ($("#search-page-size") && [...$("#search-page-size").options].some((option) => Number(option.value) === state.searchPageSize)) {
    $("#search-page-size").value = String(state.searchPageSize);
  }
  const fullRunPayload = incoming.length > state.searchPageSize
    && Number(combinedPayload.loaded_count || incoming.length) === incoming.length;
  const providerPageResults = incoming.filter((result) => Number(result.source_page) === page);
  const currentPageResults = providerPageResults.length
    ? providerPageResults
    : fullRunPayload
      ? incoming.slice(Math.max(0, (page - 1) * state.searchPageSize), page * state.searchPageSize)
      : incoming;
  state.searchCurrentPageIds = currentPageResults.map((result) => result.id);
  state.searchHasMore = Boolean(combinedPayload.has_more ?? pagination.has_more ?? false);
  state.searchNextPage = combinedPayload.next_page ?? pagination.next_page ?? (state.searchHasMore ? page + 1 : null);
  state.searchNextCursor = combinedPayload.next_cursor ?? pagination.next_cursor ?? null;
  state.searchRun = { ...(append ? state.searchRun : {}), ...combinedPayload, results: state.searchResults };
  state.searchLastRequest = {
    keyword: combinedPayload.keyword || state.searchLastRequest?.keyword || $("#search-keyword")?.value.trim(),
    scope: combinedPayload.scope || state.searchLastRequest?.scope || $("#search-scope")?.value || "web",
    language: combinedPayload.language || state.searchLastRequest?.language || selectedSearchLanguage(),
    limit: state.searchPageSize,
    page_size: state.searchPageSize,
    ...(combinedPayload.investigation_id ? { investigation_id: combinedPayload.investigation_id } : {}),
  };
  const runId = currentSearchRunId();
  state.searchSelectedIds = new Set(runId ? state.searchSelectionsByRun.get(runId) || [] : []);
  state.searchError = "";
  state.searchErrorInfo = null;
  state.searchErrorSource = null;
  if (combinedPayload.keyword) $("#search-keyword").value = combinedPayload.keyword;
  if (combinedPayload.scope && $("#search-scope")) $("#search-scope").value = combinedPayload.scope;
  if (runId) state.searchSelectionsByRun.set(runId, new Set(state.searchSelectedIds));
  upsertSearchHistory({ ...combinedPayload, id: runId, loaded_count: state.searchResults.length });
  renderSearchResults();
}

async function openSearchHistoryRun(runId) {
  if (!runId || state.searchBusy) return;
  const requestContext = beginSearchRequest();
  const currentId = currentSearchRunId();
  if (currentId) state.searchSelectionsByRun.set(currentId, new Set(state.searchSelectedIds));
  state.searchBusy = true;
  renderSearchBusyState("正在重新打开查询运行…");
  try {
    let payload;
    if (runId === currentId && state.searchResults.length) payload = state.searchRun;
    else {
      const rawDestinationId = $("#search-destination")?.value;
      const destinationId = rawDestinationId === UNASSIGNED_VALUE ? unclassifiedInvestigation()?.id : rawDestinationId;
      const params = new URLSearchParams({ visibility: "all" });
      if (destinationId && destinationId !== NEW_INVESTIGATION_VALUE) params.set("investigation_id", destinationId);
      payload = await api(`${API_ROUTES.searchRun(runId)}?${params}`);
    }
    if (!searchRequestIsCurrent(requestContext)) return;
    applySearchPayload(payload, { append: false });
    const run = normalizeSearchRun(payload);
    state.searchSelectedIds = new Set(state.searchSelectionsByRun.get(run.id) || []);
    renderSearchResults();
    if (["failed", "partial_failure"].includes(run.status) || run.error_detail || run.structured_error) {
      showSearchError(run.error_detail || run.structured_error || run.error, "query");
    } else {
      setSearchCompletionStatus(payload, "已重新打开查询");
    }
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    showSearchError(error, "query");
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      $("#search-submit").disabled = false;
      $("#search-submit").textContent = "开始搜索";
      renderSearchResultSummary();
      updateSearchSelectionCount();
    }
  }
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
  if (!selection) return "选择这条线索";
  const status = LABELS.intakeStatus[selection.intake_status || selection.status] || selection.status;
  if (selection.intake_status === "failed") {
    return selection.retryable === false ? `${status} · 需更换来源或检查配置` : `${status} · 可重试`;
  }
  return `采集箱已有 · ${status || "可复用"}`;
}

function searchResultRelevance(result) {
  const relevance = result?.topic_relevance || {};
  const level = ["likely", "uncertain", "unlikely"].includes(relevance.level) ? relevance.level : "unknown";
  const defaults = {
    likely: ["与专题相关", "标题命中专题词，可优先处理。"],
    uncertain: ["相关性存疑", "摘要可能涉及专题，请先判断是否值得处理。"],
    unlikely: ["可能无关", "默认不进入待处理；仍可手动选择。"],
    unknown: ["尚未初筛", "缺少专题上下文，请人工判断。"],
  };
  return {
    level,
    label: relevance.label || defaults[level][0],
    reason: relevance.reason || defaults[level][1],
  };
}

function visibleSearchResults() {
  const query = $("#search-result-filter")?.value.trim().toLocaleLowerCase() || "";
  const status = $("#search-result-state")?.value || "all";
  return state.searchResults.filter((result) => {
    const linked = searchResultLinkedToDestination(result);
    const selected = state.searchSelectedIds.has(result.id);
    const failed = Boolean(result.selection?.last_error || result.selection?.error || result.selection?.intake_status === "failed");
    const relevance = searchResultRelevance(result);
    if (status === "available" && (linked || result.selection && !failed)) return false;
    if (status === "selected" && !selected) return false;
    if (status === "failed" && !failed) return false;
    if (["likely", "uncertain", "unlikely"].includes(status) && relevance.level !== status) return false;
    if (!query) return true;
    return [result.title, result.snippet, result.site, result.channel, result.provider, result.original_url]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query);
  });
}

function searchClueCompleteness(result) {
  const fields = [result.title, result.snippet, result.published_at].filter(Boolean).length;
  return `${fields}/3 个线索字段`;
}

function renderSearchResults() {
  const root = $("#search-results");
  if (!root) return;
  const visible = visibleSearchResults();
  $("#search-result-tools").hidden = state.searchResults.length === 0;
  root.innerHTML = visible.length ? visible.map((result) => {
    const linked = searchResultLinkedToDestination(result);
    const checked = state.searchSelectedIds.has(result.id);
    const relevance = searchResultRelevance(result);
    const selectionError = result.selection?.error || result.selection?.last_error;
    const selectionStage = selectionError ? normalizeOperationalError(selectionError, "fetch").stage : "fetch";
    const retryAction = result.selection?.retryable
      ? `<button class="btn btn-ghost warning" type="button" data-search-retry="${escapeHtml(result.id)}">${selectionStage === "generate" ? "重新生成草稿" : "重试抓取"}</button>`
      : "";
    return `
    <article class="search-result ${linked ? "selected" : result.selection ? "existing" : ""}" role="listitem">
      <label class="search-select">
        <input type="checkbox" value="${escapeHtml(result.id)}" ${linked ? "disabled" : checked ? "checked" : ""}>
        <span>${escapeHtml(searchSelectionLabel(result, linked))}</span>
      </label>
      <div class="search-result-body">
        <div class="search-result-meta">
          <span class="search-relevance ${escapeHtml(relevance.level)}" title="${escapeHtml(relevance.reason)}">${escapeHtml(relevance.label)}</span>
          <span>检索排名 #${result.rank || "-"}</span>
          <span>${escapeHtml(result.site || "未知站点")}</span>
          <span>${escapeHtml(result.channel || result.provider || "未知渠道")}</span>
          <span>${formatDate(result.published_at, true)}</span>
          <span class="clue-completeness">${searchClueCompleteness(result)} · 不是可信度</span>
        </div>
        <h3>${escapeHtml(result.title || "无标题")}</h3>
        <p class="search-relevance-reason">${escapeHtml(relevance.reason)}</p>
        ${result.snippet ? `<p>${escapeHtml(result.snippet)}</p>` : '<p class="muted">检索后端未返回摘要。</p>'}
        <a href="${escapeHtml(result.original_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(result.original_url)}</a>
        ${selectionError ? renderOperationalError({ ...(typeof selectionError === "object" ? selectionError : { message: selectionError }), retryable: result.selection?.retryable }, { stage: result.selection?.error_stage || "fetch", compact: true, actionHtml: retryAction }) : ""}
        <details class="search-result-technical">
          <summary>查询追踪</summary>
          <div class="search-result-footer">
            <small>查询运行：${escapeHtml(result.query_run_id || currentSearchRunId() || "未知")} · 结果：${escapeHtml(result.id || "未知")}</small>
          </div>
        </details>
      </div>
    </article>`;
  }).join("") : state.searchResults.length
    ? '<div class="search-empty">已加载结果中没有符合当前筛选条件的条目。</div>'
    : '<div class="search-empty">没有匹配结果。PLDR 不会用演示数据填充空结果。</div>';
  $$("input[type='checkbox']", root).forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.searchSelectedIds.add(input.value);
    else state.searchSelectedIds.delete(input.value);
    const runId = currentSearchRunId();
    if (runId) state.searchSelectionsByRun.set(runId, new Set(state.searchSelectedIds));
    if (!input.checked && $("#search-result-state")?.value === "selected") {
      renderSearchResults();
      return;
    }
    updateSearchSelectionCount();
  }));
  renderSearchResultSummary();
  updateSearchSelectionCount();
}

function renderSearchResultSummary() {
  const loaded = state.searchResults.length;
  const run = state.searchRun || {};
  const totalKnown = Boolean(run.total_known ?? run.pagination?.total_known);
  const total = run.available_count ?? run.total_estimate ?? run.pagination?.available_count ?? null;
  const maxLoaded = Number(run.max_loaded_results ?? run.pagination?.max_loaded_results ?? 0);
  const reachedCap = maxLoaded > 0 && loaded >= maxLoaded;
  $("#search-loaded-count").textContent = `已加载 ${loaded} 条`;
  $("#search-run-summary").textContent = totalKnown && total != null
    ? `服务端确认共有 ${Number(total)} 条可用结果`
    : "全网总数未知；这里只统计已经加载的结果";
  const row = $("#search-load-more-row");
  row.hidden = (!state.searchHasMore && !reachedCap) || !loaded;
  $("#search-load-more").disabled = state.searchBusy || !state.searchHasMore;
  $("#search-load-more").textContent = state.searchHasMore ? "继续加载下一页" : `已达 ${maxLoaded} 条上限`;
  $("#search-load-more-note").textContent = reachedCap
    ? `本次查询最多保留 ${maxLoaded} 条；如需更多结果，请细化关键词后重新查询。`
    : state.searchHasMore
    ? `当前查询按每页 ${state.searchPageSize} 条继续；下一页会加入现有 ${loaded} 条，已有选择不会丢失。`
    : "当前查询没有更多可加载页面。";
}

function selectableSearchIds(ids) {
  const idSet = new Set(ids);
  return state.searchResults.filter((result) => idSet.has(result.id) && !searchResultLinkedToDestination(result)).map((result) => result.id);
}

function selectSearchResults(ids) {
  const next = new Set(state.searchSelectedIds);
  const available = selectableSearchIds(ids);
  const remaining = Math.max(0, 100 - next.size);
  available.slice(0, remaining).forEach((id) => next.add(id));
  state.searchSelectedIds = next;
  const runId = currentSearchRunId();
  if (runId) state.searchSelectionsByRun.set(runId, new Set(next));
  renderSearchResults();
  if (available.length > remaining) toast("单批最多处理 100 条；已选择前 100 条，其余保留在结果中。", "info", 6500);
}

function selectVisibleSearchPage() {
  const currentPageIds = new Set(state.searchCurrentPageIds);
  const visibleIds = visibleSearchResults()
    .filter((result) => currentPageIds.has(result.id))
    .map((result) => result.id);
  selectSearchResults(visibleIds);
}

function clearSearchSelection() {
  state.searchSelectedIds.clear();
  const runId = currentSearchRunId();
  if (runId) state.searchSelectionsByRun.set(runId, new Set());
  renderSearchResults();
}

function updateSearchSelectionCount() {
  const loadedIds = new Set(state.searchResults.map((result) => result.id));
  const selected = [...state.searchSelectedIds].filter((id) => loadedIds.has(id));
  const button = $("#search-select");
  button.disabled = state.searchBusy || selected.length === 0;
  button.textContent = selected.length ? `处理已选 ${selected.length} 条` : "加入专题并开始处理";
  $("#search-selection-count").textContent = selected.length
    ? `已选择 ${selected.length} 条 · 已加载 ${state.searchResults.length} 条`
    : `已加载 ${state.searchResults.length} 条，未选择`;
}

function renderSearchBusyState(message) {
  $("#search-status").className = "search-status busy";
  $("#search-status").innerHTML = `<span class="status-spinner" aria-hidden="true"></span><strong>${escapeHtml(message)}</strong><span>已有结果与选择会保留。</span>`;
  const submit = $("#search-submit");
  submit.disabled = true;
  submit.textContent = "搜索中…";
  $("#search-destination").disabled = true;
  $("#search-load-more").disabled = true;
  updateSearchSelectionCount();
}

function setSearchCompletionStatus(payload, prefix = "检索完成") {
  const run = payload?.run && typeof payload.run === "object" ? { ...payload, ...payload.run } : payload || {};
  const loaded = state.searchResults.length;
  const returned = Number(run.returned_count ?? searchPayloadResults(payload).length ?? 0);
  const channel = run.channel || run.provider || "外部检索后端";
  const latency = Number(run.latency_ms || 0);
  $("#search-status").className = "search-status ok";
  $("#search-status").innerHTML = `<strong>${escapeHtml(prefix)}</strong><span>本页返回 ${returned} 条，当前已加载 ${loaded} 条 · ${escapeHtml(channel)}${latency ? ` · ${latency} ms` : ""}</span><small>排名只表示检索顺序，不表示来源可靠性或内容真实性。</small>`;
  renderSearchResultSummary();
}

function showSearchError(error, stage = "query") {
  state.searchError = error?.message || objectMessage(error) || "未知错误";
  state.searchErrorInfo = normalizeOperationalError(error, stage);
  state.searchErrorSource = error;
  const retryAction = stage === "query" && state.searchErrorInfo.retryable
    ? '<button class="btn btn-ghost warning" type="button" data-search-retry-query>重试这次查询</button>'
    : "";
  $("#search-status").className = "search-status error";
  $("#search-status").innerHTML = `${renderOperationalError(error, { stage, actionHtml: retryAction })}<small>本次没有用假数据补齐，未生成演示结果。</small>`;
  renderSearchResultSummary();
}

function revealSearchError(message) {
  toast(message, "error", 7500);
  const status = $("#search-status");
  if (!status) return;
  status.setAttribute("tabindex", "-1");
  status.scrollIntoView({ behavior: "smooth", block: "center" });
  status.focus({ preventScroll: true });
}

async function retryCurrentSearchQuery() {
  if (state.searchBusy) return;
  const requestContext = beginSearchRequest();
  const source = state.searchErrorSource?.payload?.detail || state.searchErrorSource || state.searchRun?.error_detail || {};
  const runId = source.query_run_id || currentSearchRunId();
  if (!state.searchLastRequest) {
    toast("缺少可恢复的查询上下文，请重新输入关键词发起查询。", "error", 6200);
    return;
  }
  const attemptedPage = runId
    ? Number(source.attempted_page || (state.searchRun?.status === "partial_failure" ? state.searchPage + 1 : state.searchPage || 1))
    : 1;
  const request = {
    ...state.searchLastRequest,
    page: attemptedPage,
    ...(runId ? { query_run_id: runId } : {}),
    ...(attemptedPage > 1 ? { pageno: attemptedPage, cursor: String(attemptedPage) } : {}),
  };
  state.searchBusy = true;
  renderSearchBusyState("正在重试原查询…");
  try {
    const payload = await api(API_ROUTES.search, { method: "POST", body: JSON.stringify(request) });
    if (!searchRequestIsCurrent(requestContext)) return;
    state.searchLastRequest = request;
    applySearchPayload(payload, { append: attemptedPage > 1 });
    setSearchCompletionStatus(payload, "查询重试成功");
    await loadSearchHistory(currentSearchDestinationId()).catch(() => null);
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    showSearchError(error, "query");
    revealSearchError(`查询重试失败：${error.message || "请查看诊断详情"}`);
    await loadSearchHistory(currentSearchDestinationId()).catch(() => null);
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      $("#search-submit").disabled = false;
      $("#search-submit").textContent = "开始搜索";
      renderSearchResultSummary();
      updateSearchSelectionCount();
    }
  }
}

async function submitExternalSearch(event) {
  event.preventDefault();
  if (state.searchBusy) return;
  const keyword = $("#search-keyword").value.trim();
  if (keyword.length < 2) {
    showSearchError({ title: "关键词太短", message: "至少输入 2 个字符，系统才能发起检索。", impact: "尚未向外部服务提交请求。", next_action: "补充更具体的人、机构、地点或事件关键词。", retryable: false }, "query");
    return;
  }
  const requestContext = beginSearchRequest();
  state.searchBusy = true;
  state.searchError = "";
  state.searchErrorInfo = null;
  renderSearchBusyState("正在搜索公开资料…");
  try {
    const destinationId = $("#search-destination").value;
    const destination = destinationId === UNASSIGNED_VALUE
      ? unclassifiedInvestigation()
      : state.investigations.find((item) => item.id === destinationId);
    const context = ["server", "system"].includes(destination?.sync_mode) ? { investigation_id: destination.id } : {};
    const language = selectedSearchLanguage(keyword);
    const pageSize = Number($("#search-page-size").value || 20);
    const request = {
      keyword,
      scope: $("#search-scope").value,
      language,
      limit: pageSize,
      page_size: pageSize,
      page: 1,
      ...context,
    };
    const previousRunId = currentSearchRunId();
    if (previousRunId) state.searchSelectionsByRun.set(previousRunId, new Set(state.searchSelectedIds));
    state.searchRun = null;
    state.searchResults = [];
    state.searchCurrentPageIds = [];
    state.searchSelectedIds = new Set();
    state.searchPage = 1;
    state.searchHasMore = false;
    state.searchNextPage = null;
    state.searchNextCursor = null;
    state.searchLastRequest = request;
    renderSearchResults();
    const payload = await api(API_ROUTES.search, { method: "POST", body: JSON.stringify(request) });
    if (!searchRequestIsCurrent(requestContext)) return;
    applySearchPayload(payload, { append: false });
    setSearchCompletionStatus(payload);
    loadSearchHistory(destination?.id).catch(() => null);
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    showSearchError(error, "query");
    revealSearchError(`搜索失败：${error.message || "请查看诊断详情"}`);
    if (!state.searchResults.length) renderSearchResults();
    await loadSearchHistory(currentSearchDestinationId()).catch(() => null);
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      const submit = $("#search-submit");
      submit.disabled = false;
      submit.textContent = "开始搜索";
      renderSearchResultSummary();
      updateSearchSelectionCount();
    }
  }
}

async function loadMoreExternalSearch() {
  if (state.searchBusy || !state.searchHasMore || !state.searchLastRequest) return;
  const requestContext = beginSearchRequest();
  state.searchBusy = true;
  renderSearchBusyState("正在加载下一页…");
  try {
    const request = {
      ...state.searchLastRequest,
      page: Number(state.searchNextPage || state.searchPage + 1),
      pageno: Number(state.searchNextPage || state.searchPage + 1),
      query_run_id: currentSearchRunId(),
      ...(state.searchNextCursor ? { cursor: state.searchNextCursor } : {}),
    };
    const payload = await api(API_ROUTES.search, { method: "POST", body: JSON.stringify(request) });
    if (!searchRequestIsCurrent(requestContext)) return;
    state.searchLastRequest = request;
    applySearchPayload(payload, { append: true });
    setSearchCompletionStatus(payload, "下一页已加载");
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    showSearchError(error, "query");
    revealSearchError(`下一页加载失败：${error.message || "请查看诊断详情"}`);
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      $("#search-submit").disabled = false;
      $("#search-submit").textContent = "开始搜索";
      renderSearchResultSummary();
      updateSearchSelectionCount();
    }
  }
}

async function submitSelectedSearchResults() {
  const loadedIds = new Set(state.searchResults.map((result) => result.id));
  const selectedIds = [...state.searchSelectedIds].filter((id) => loadedIds.has(id));
  if (!selectedIds.length || state.searchBusy) return;
  if (selectedIds.length > 100) {
    showSearchError({ title: "单批选择过多", message: `当前选择了 ${selectedIds.length} 条，单批最多处理 100 条。`, impact: "尚未提交处理任务，已有选择仍然保留。", next_action: "取消一部分选择后分批处理。", retryable: false }, "link");
    revealSearchError("单批最多处理 100 条；请减少选择后重试。");
    return;
  }
  let intent;
  try {
    intent = destinationIntent("search");
  } catch (error) {
    showSearchError(error, "link");
    revealSearchError(`目标专题不可用：${error.message || "请重新选择"}`);
    return;
  }
  const requestContext = beginSearchRequest();
  state.searchBusy = true;
  $("#search-destination").disabled = true;
  const button = $("#search-select");
  button.disabled = true;
  button.textContent = "正在提交…";
  $("#search-status").className = "search-status busy";
  $("#search-status").innerHTML = '<strong>正在提交逐条处理任务</strong><span>搜索摘要不会进入证据链；每条资料会独立显示抓取与候选生成进度。</span>';
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
    if (!searchRequestIsCurrent(requestContext) || !$("#search-modal").open) {
      refreshInvestigationDirectory().catch(() => null);
      toast("处理请求已被服务端接受；你已切换页面，所以没有自动跳转。", "info", 7500);
      return;
    }
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
    if (investigation && taskRows.length) state.investigationTasks.set(investigation.id, [...taskRows, ...(state.investigationTasks.get(investigation.id) || [])]);

    const intakeIds = responseEntries.map((entry) => entry.intake_item_id).filter(Boolean);
    let association = { linked: 0, failed: 0, mode: "server" };
    if (investigation && investigation.sync_mode === "local" && intakeIds.length) {
      association = await associateInvestigationObjects(investigation, "intake", intakeIds, { origin: "external_search" });
    } else if (isServerInvestigation(investigation) && !asynchronous && intakeIds.length) {
      association = await associateInvestigationObjects(investigation, "intake", intakeIds, { origin: "external_search_legacy" });
    }

    if (!searchRequestIsCurrent(requestContext) || !$("#search-modal").open) {
      refreshInvestigationDirectory().catch(() => null);
      toast("处理任务已保存；你已切换页面，所以没有改变当前专题。", "info", 7500);
      return;
    }

    state.searchSelectedIds.clear();
    const runId = currentSearchRunId();
    if (runId) state.searchSelectionsByRun.set(runId, new Set());
    closeExternalSearchModal();
    renderInvestigationHome();
    if (investigation) await openInvestigation(investigation.id, "overview");
    else showInvestigationHome();
    if (asynchronous) toast(`已提交 ${responseEntries.length || selectedIds.length} 条任务；专题会逐条显示抓取、生成和审核进度。`, "success", 6500);
    else {
      const updates = new Map((payload.results || []).map((entry) => [entry.result_id, entry.result]));
      state.searchResults = state.searchResults.map((result) => updates.get(result.id) || result);
      const failures = (payload.results || []).filter((entry) => entry.intake_status === "failed").length;
      toast(failures ? `已处理 ${responseEntries.length} 项，其中 ${failures} 项抓取失败；可在专题中查看原因。` : `已处理 ${responseEntries.length} 项并进入人工候选流程。`, failures ? "error" : "success", 6500);
    }
    if (association.failed) toast(`材料已进入采集箱，但 ${association.failed} 条专题关联失败；没有声称关联成功。`, "error", 7500);
    await refreshIntakeData().catch(() => null);
    renderInvestigationHome();
    if (!investigation && intakeIds[0]) await openIntakeModal(intakeIds[0], false, null);
    if (isServerInvestigation(investigation)) await loadInvestigationWorkspace(investigation.id, { quiet: true });
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    if (requestAccepted) {
      closeExternalSearchModal();
      showInvestigationHome();
      toast("处理请求已被服务端接受，但专题关联或页面刷新没有完成；请在待处理队列核对。", "error", 9000);
    } else {
      showSearchError(error, "link");
      revealSearchError(`提交处理任务失败：${error.message || "请查看诊断详情"}`);
    }
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      button.textContent = "加入专题并开始处理";
      updateSearchSelectionCount();
    }
  }
}

async function retryExternalSearchResult(resultId) {
  if (state.searchBusy) return;
  const requestContext = beginSearchRequest();
  state.searchBusy = true;
  renderSearchBusyState("正在重试抓取原始页面…");
  try {
    const payload = await api(`/pldr-api/v1/search/results/${encodeURIComponent(resultId)}/retry`, { method: "POST" });
    if (!searchRequestIsCurrent(requestContext)) return;
    state.searchResults = state.searchResults.map((result) => (result.id === resultId ? payload.result : result));
    if (payload.intake_status === "failed") {
      showSearchError(payload.error || payload.result?.selection?.last_error || "未知错误", "fetch");
      revealSearchError("原始页面重试仍然失败；该结果已保留失败原因。 ");
    }
    else {
      $("#search-status").className = "search-status ok";
      $("#search-status").innerHTML = "<strong>原始页面重试完成</strong><span>条目仍需候选审核和人工确认，尚未自动进入正式档案。</span>";
    }
    renderSearchResults();
    await refreshIntakeData();
  } catch (error) {
    if (!searchRequestIsCurrent(requestContext)) return;
    showSearchError(error, "fetch");
    revealSearchError(`原始页面重试失败：${error.message || "请查看诊断详情"}`);
  } finally {
    if (searchRequestIsCurrent(requestContext)) {
      state.searchBusy = false;
      $("#search-destination").disabled = false;
      $("#search-submit").disabled = false;
      $("#search-submit").textContent = "开始搜索";
      updateSearchSelectionCount();
    }
  }
}

async function submitImport(event) {
  event.preventDefault();
  const requestSerial = ++state.importRequestSerial;
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
  const language = "auto";
  const title = $("#import-title").value.trim();
  const published = $("#import-published").value;
  const submit = $("#import-submit");
  submit.disabled = true;
  submit.textContent = mode === "url" || mode === "rss" ? "正在读取并保存" : "正在保存";
  $("#import-result").className = "import-result";
  $("#import-result").textContent = "正在保存原始资料；保存后由后台继续整理，不需要停在这里等待…";

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
      result = await api("/pldr-api/v1/intake/files?defer_candidates=true", { method: "POST", body });
    } else if (mode === "text") {
      const body = {
        text: $("#import-text").value,
        source_description: sourceName,
        title: title || null,
        published_at: published ? new Date(published).toISOString() : null,
        language,
      };
      result = await api("/pldr-api/v1/intake/text?defer_candidates=true", { method: "POST", body: JSON.stringify(body) });
    } else if (mode === "rss") {
      const body = { url, source_name: sourceName || "Imported RSS", language };
      result = await api("/pldr-api/v1/import/rss?defer_candidates=true", { method: "POST", body: JSON.stringify(body) });
    } else {
      const body = { url, source_name: sourceName || null, title: title || null, language };
      result = await api("/pldr-api/v1/import/url?defer_candidates=true", { method: "POST", body: JSON.stringify(body) });
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

    if (requestSerial !== state.importRequestSerial || !$("#import-modal").open) {
      refreshData({ keepSelection: true, quiet: true }).catch(() => null);
      refreshInvestigationDirectory().catch(() => null);
      toast(`已保存 ${count} 条材料；你已切换页面，所以没有自动跳转。`, association.failed ? "error" : "info", 8000);
      return;
    }

    await refreshData({ keepSelection: true, quiet: true });
    await refreshInvestigationDirectory();
    if (requestSerial !== state.importRequestSerial || !$("#import-modal").open) {
      toast(`已保存 ${count} 条材料；你已切换页面，所以没有自动跳转。`, association.failed ? "error" : "info", 8000);
      return;
    }
    closeImportModal();
    if (investigation && association.failed === 0) {
      toast(investigation.sync_mode === "local"
        ? `已导入 ${count} 条真实材料；专题归类仅保存在此浏览器。`
        : `已保存并加入专题：${count} 条；系统会在后台整理，完成后出现在“等待确认”。`, "success", 7000);
      await openInvestigation(investigation.id, "overview");
      const actionable = items.find((item) => ["candidate_ready", "generation_failed", "failed"].includes(item.status));
      if (actionable) {
        await openIntakeModal(actionable.id, false, isServerInvestigation(investigation) ? investigation.id : null);
      }
    } else {
      toast(association.failed
        ? `材料已真实进入采集箱，但专题关联失败：${association.errors?.[0] || "未知错误"}`
        : `材料已进入待归类采集箱：${count} 条。`, association.failed ? "error" : "success", 8000);
      showInvestigationHome();
      await openIntakeModal(items[0].id);
    }
  } catch (error) {
    if (requestSerial !== state.importRequestSerial) {
      toast(persistedCount
        ? `服务端已保存 ${persistedCount} 条材料，但后续归类失败：${error.message || "未知错误"}`
        : `后台导入失败：${error.message || "未知错误"}`, "error", 9000);
      return;
    }
    $("#import-result").className = `import-result ${persistedCount ? "success" : "error"}`;
    $("#import-result").textContent = persistedCount
      ? `服务端已保存 ${persistedCount} 条材料，但后续归类或页面刷新失败：${error.message}。请到采集箱核对。`
      : `导入失败：${error.message}。未显示虚假成功。`;
    if (persistedCount) toast(`材料已保存，但后续归类或页面刷新失败：${error.message}`, "error", 9000);
  } finally {
    if (requestSerial === state.importRequestSerial) {
      submit.disabled = false;
      submit.textContent = "提交到采集箱";
    }
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

function recordAllowsAction(item, ...names) {
  const allowed = item?.allowed_actions;
  if (Array.isArray(allowed)) return names.some((name) => allowed.includes(name));
  if (allowed && typeof allowed === "object") return names.some((name) => allowed[name] === true || allowed[name]?.allowed === true);
  return false;
}

function intakeRecordActionAllowed(item, action) {
  const archived = state.intakeVisibility === "archived" || recordIsArchived(item);
  // The API derives these actions from the real ReviewTask state. Do not guess
  // whether a record is running from the intake status and hide an action that
  // the scoped/global serializer explicitly allowed. Confirmed records remain
  // a client-side hard stop so the formal archive boundary is never presented
  // as deletable even if an older response is stale.
  if (["confirmed", "accepted"].includes(item?.status)) return false;
  if (action === "restore-record") return archived && recordAllowsAction(item, "restore");
  if (archived) return false;
  if (action === "remove-record") return Boolean(state.intakeScopeInvestigationId) && recordAllowsAction(item, "remove", "remove_from_investigation");
  if (action === "archive-record") return !state.intakeScopeInvestigationId && recordAllowsAction(item, "archive");
  return false;
}

function renderIntakeRecordActions(item) {
  if (intakeRecordActionAllowed(item, "restore-record")) {
    return '<button class="btn btn-ghost record-restore" type="button" data-intake-action="restore-record">恢复</button>';
  }
  if (intakeRecordActionAllowed(item, "remove-record")) {
    return '<button class="btn btn-ghost record-delete" type="button" data-intake-action="remove-record">从本专题移除</button>';
  }
  if (intakeRecordActionAllowed(item, "archive-record")) {
    return '<button class="btn btn-ghost record-delete" type="button" data-intake-action="archive-record">删除</button>';
  }
  return "";
}

function renderIntakeList() {
  const root = $("#intake-list");
  if (!root) return;
  const activeCount = state.intakeItems.filter((item) => ACTIVE_INTAKE_STATUSES.has(item.status)).length;
  const globalActiveCount = state.globalIntakeItems.filter((item) => ACTIVE_INTAKE_STATUSES.has(item.status)).length;
  const readyItems = state.intakeItems.filter((item) => item.status === "candidate_ready" && !recordIsArchived(item));
  const selectedReady = readyItems.filter((item) => state.selectedIntakeIds.has(item.id));
  const badge = $("#intake-count");
  if (badge) badge.textContent = String(globalActiveCount);
  $$('[data-intake-visibility]').forEach((button) => button.classList.toggle("active", button.dataset.intakeVisibility === state.intakeVisibility));
  $("#intake-summary").textContent = state.intakeVisibility === "archived"
    ? `${state.intakeScopeInvestigationId ? "本专题已移除" : "全局已删除"} · ${state.intakeItems.length} 条记录`
    : state.intakeScopeInvestigationId
      ? `本专题 · ${activeCount} 条待处理 / ${state.intakeItems.length} 条记录`
      : `全局 · ${activeCount} 条待处理 / ${state.intakeItems.length} 条记录`;
  root.innerHTML = state.intakeItems.length ? `${readyItems.length ? `<div class="intake-batch-bar"><label><input type="checkbox" data-intake-select-all ${selectedReady.length === readyItems.length ? "checked" : ""}> 全选待处理</label><span>已选 ${selectedReady.length} 条</span><button class="btn btn-primary" type="button" data-intake-batch="accept" ${selectedReady.length ? "" : "disabled"}>批量加入专题</button><button class="btn btn-ghost" type="button" data-intake-batch="reject" ${selectedReady.length ? "" : "disabled"}>批量忽略</button></div>` : ""}${state.intakeItems.map((item) => `
    <div class="intake-list-row" role="listitem">
      ${item.status === "candidate_ready" && !recordIsArchived(item) ? `<label class="intake-select" title="选择这条材料"><input type="checkbox" data-intake-select="${escapeHtml(item.id)}" ${state.selectedIntakeIds.has(item.id) ? "checked" : ""}><span class="sr-only">选择 ${escapeHtml(intakeTitle(item))}</span></label>` : ""}
      <button class="intake-item ${item.id === state.selectedIntakeId ? "active" : ""}" type="button" data-intake-id="${escapeHtml(item.id)}">
      <span class="intake-type">${escapeHtml(LABELS.inputType[item.input_type] || item.input_type)}</span>
      <strong>${escapeHtml(intakeTitle(item))}</strong>
      <small>${state.intakeVisibility === "archived" || recordIsArchived(item) ? "已删除" : escapeHtml(LABELS.intakeStatus[item.status] || item.status)} · ${formatDate(item.archived_at || item.removed_at || item.created_at, true)}</small>
      ${item.analysis?.relevance ? `<em>${escapeHtml(({ relevant: "与专题相关", uncertain: "相关性待确认", not_relevant: "可能与专题无关" })[item.analysis.relevance] || item.analysis.relevance)}</em>` : ""}
      ${item.error ? `<em>${escapeHtml(normalizeOperationalError(item.error, item.status === "generation_failed" ? "generate" : "fetch").title)}</em>` : ""}
      </button>
    </div>
  `).join("")}` : `<p class="muted intake-empty">${state.intakeVisibility === "archived" ? "没有可恢复的已删除记录。" : "当前没有待处理材料。已完成记录可在专题操作记录中查看。"}</p>`;
}

function defaultConfirmationForItem(item) {
  const event = candidateList(item, "event")[0]?.machine?.fields || {};
  const evidence = candidateList(item, "evidence");
  const defaults = candidateConfirmationDefaults(item);
  const normalizedTitle = String(event.title || "").trim().toLocaleLowerCase();
  const mergeTarget = normalizedTitle
    ? (state.intakeOptions.events || []).find((option) => String(option.title || "").trim().toLocaleLowerCase() === normalizedTitle)
    : null;
  return {
    disposition: mergeTarget ? "merge" : "create",
    analyst: "analyst",
    merge_event_id: mergeTarget?.id || null,
    event: {
      title: event.title || item.title || "",
      summary: event.summary || "",
      event_type: event.event_type || "incident",
      start_at: normalizeEventTimeForConfirmation(event.start_at || event.event_time, { strict: false }),
      location_name: event.location_name || "",
      importance: event.importance || "medium",
    },
    entities: candidateList(item, "entity").map((candidate) => {
      const fields = candidate.machine?.fields || {};
      return { candidate_key: candidate.candidate_key, action: "create", name: fields.name || "", entity_type: fields.entity_type || "organization", aliases: Array.isArray(fields.aliases) ? fields.aliases : [], role: fields.role || "related", merge_entity_id: null };
    }),
    claims: candidateList(item, "claim").map((candidate) => {
      const fields = candidate.machine?.fields || {};
      const included = defaults.claimKeys.has(candidate.candidate_key);
      return { candidate_key: candidate.candidate_key, action: included ? "create" : "exclude", text: fields.text || "", status: fields.status === "contested" ? "contested" : (defaults.supportedClaimKeys.has(candidate.candidate_key) ? "supported" : "unverified"), confidence: unitIntervalValue(fields.confidence, 0.5), temporal_scope: fields.temporal_scope || "", merge_claim_id: null };
    }),
    evidence: evidence.map((candidate) => {
      const fields = candidate.machine?.fields || {};
      return { candidate_key: candidate.candidate_key, action: defaults.evidenceKeys.has(candidate.candidate_key) ? "include" : "exclude", snippet: fields.snippet || "", stance: fields.stance || "context", strength: unitIntervalValue(fields.strength, 0.7), note: fields.note || "" };
    }),
  };
}

function candidateConfirmationDefaults(item) {
  const evidence = candidateList(item, "evidence");
  const validEvidenceKeys = new Set(
    evidence.filter((candidate) => !candidate.validation_error).map((candidate) => candidate.candidate_key),
  );
  const relations = Array.isArray(item?.candidate_generation?.relations)
    ? item.candidate_generation.relations
    : [];
  const claimEvidence = relations.filter((relation) => relation?.type === "claim_evidence");
  const evidenceKeys = new Set();
  const claimKeys = new Set();
  const supportedClaimKeys = new Set();
  claimEvidence.forEach((relation) => {
    if (!validEvidenceKeys.has(relation.from) || relation.valid === false) return;
    evidenceKeys.add(relation.from);
    claimKeys.add(relation.to);
    const candidate = evidence.find((entry) => entry.candidate_key === relation.from);
    if (candidate?.machine?.fields?.stance === "supports") supportedClaimKeys.add(relation.to);
  });
  return {
    evidenceKeys,
    claimKeys,
    supportedClaimKeys,
    excludedEvidenceCount: evidence.length - evidenceKeys.size,
    excludedClaimCount: candidateList(item, "claim").filter((candidate) => !claimKeys.has(candidate.candidate_key)).length,
  };
}

async function handleIntakeBatch(action) {
  const ids = state.intakeItems.filter((item) => state.selectedIntakeIds.has(item.id) && item.status === "candidate_ready").map((item) => item.id);
  if (!ids.length || state.intakeActionBusy) return;
  const workspaceId = state.intakeScopeInvestigationId
    || (isServerInvestigation(activeInvestigation()) ? state.activeInvestigationId : null);
  const verb = action === "accept" ? "加入专题" : "忽略";
  if (!window.confirm(`确认批量${verb}选中的 ${ids.length} 条材料？`)) return;
  setIntakeActionBusy(true);
  let completed = 0;
  const failures = [];
  const confirmedEventByTitle = new Map();
  try {
    for (const id of ids) {
      try {
        let item = state.intakeItems.find((candidate) => candidate.id === id);
        if (!item?.material || !Array.isArray(item.candidates)) item = await loadIntakeDetail(id);
        if (action === "reject") {
          await api(intakeReviewRoute(id, "reject"), { method: "POST", body: JSON.stringify({ analyst: "analyst", reason: "批量忽略：用户确认无需纳入专题成果" }) });
        } else {
          const payload = defaultConfirmationForItem(item);
          const eventKey = String(payload.event.title || "").trim().toLocaleLowerCase();
          if (eventKey && confirmedEventByTitle.has(eventKey)) {
            payload.disposition = "merge";
            payload.merge_event_id = confirmedEventByTitle.get(eventKey);
          }
          const preview = await api(intakeReviewRoute(id, "preview"), { method: "POST", body: JSON.stringify(payload) });
          if (!preview.confirmable) throw new Error((preview.errors || []).map(validationErrorLabel).join("；") || "内容需要修改");
          const result = await api(intakeReviewRoute(id, "confirm"), { method: "POST", body: JSON.stringify(payload) });
          const eventId = result.final_event_id || result.result?.final_event_id || result.result?.formal_object_ids?.event;
          if (eventKey && eventId) confirmedEventByTitle.set(eventKey, eventId);
        }
        completed += 1;
        state.selectedIntakeIds.delete(id);
      } catch (error) {
        failures.push(`${intakeTitle(state.intakeItems.find((item) => item.id === id) || {})}：${error.message || "处理失败"}`);
      }
    }
    await refreshData({ keepSelection: false, quiet: true });
    await refreshInvestigationDirectory();
    if (workspaceId && state.activeInvestigationId === workspaceId) {
      await loadInvestigationWorkspace(workspaceId, { quiet: true });
    }
    await refreshIntakeData(null);
    toast(failures.length ? `已${verb} ${completed} 条，${failures.length} 条需要单独处理。` : `已批量${verb} ${completed} 条。`, failures.length ? "warning" : "success", 7000);
    if (failures.length) console.warn("Batch intake failures", failures);
  } finally {
    setIntakeActionBusy(false);
  }
}

function renderReadonlyCandidate(candidate) {
  const fields = candidate.machine?.fields || candidate.machine || {};
  const type = candidate.object_type;
  const title = ({ event: "整理出的事件", entity: "相关对象", claim: "关键信息", evidence: "原文依据" })[type] || `整理内容 · ${type}`;
  let body = "";
  if (type === "event") body = `<strong>${escapeHtml(fields.title || "事件标题未知")}</strong><p>${escapeHtml(fields.summary || "没有候选摘要")}</p>`;
  else if (type === "entity") body = `<strong>${escapeHtml(fields.name || "实体名称未知")}</strong><p>${escapeHtml(fields.entity_type || "类型未知")} · ${escapeHtml(fields.role || "角色未知")}</p>`;
  else if (type === "claim") body = `<blockquote>${escapeHtml(fields.text || "主张文本未知")}</blockquote><p>${escapeHtml(LABELS.claim[fields.status] || fields.status || "状态未知")}</p>`;
  else if (type === "evidence") body = `<blockquote>${escapeHtml(fields.snippet || "证据原句未知")}</blockquote><p>${escapeHtml(LABELS.stance[fields.stance] || fields.stance || "立场未知")}</p>`;
  else body = `<p>当前版本无法用语义卡展示这个候选，请在技术详情中核对。</p>`;
  return `
    <article class="candidate-card readonly semantic-candidate">
      <header><b>${escapeHtml(title)}</b><span>待处理内容</span></header>
      <div class="semantic-candidate-body">${body}</div>
      ${candidate.validation_error ? `<p class="validation-error">${escapeHtml(candidate.validation_error)}</p>` : ""}
    </article>`;
}

function renderIntakeDetail(item = null) {
  const root = $("#intake-detail");
  if (!root) return;
  if (!item) {
    root.innerHTML = '<div class="panel-empty">请选择一条材料，查看原文和系统整理结果。</div>';
    return;
  }
  const archived = state.intakeVisibility === "archived" || recordIsArchived(item);
  if (item.status === "candidate_ready" && !archived) {
    root.innerHTML = renderIntakeReview(item);
    return;
  }
  const machineCandidates = (item.candidates || []).map(renderReadonlyCandidate).join("");
  const final = item.final_object_ids || {};
  const errorValue = item.error || item.candidate_generation?.error;
  const errorStage = item.status === "generation_failed" ? "generate" : "fetch";
  const normalizedItemError = errorValue ? normalizeOperationalError(errorValue, errorStage) : null;
  const recordActions = renderIntakeRecordActions(item);
  const errorAction = normalizedItemError?.retryable !== false && item.status === "generation_failed"
    ? '<button class="btn btn-ghost" type="button" data-intake-action="regenerate">重新分析</button>'
    : normalizedItemError?.retryable !== false && item.status === "failed" && item.search?.result_id
      ? `<button class="btn btn-ghost warning" type="button" data-intake-action="retry-search" data-search-result-id="${escapeHtml(item.search.result_id)}">重试抓取</button>`
      : "";
  root.innerHTML = `
    <article class="intake-status-card ${intakeStatusClass(item.status)}">
      <div>
        <span>${archived ? "已删除 · 可恢复" : escapeHtml(LABELS.intakeStatus[item.status] || item.status)}</span>
        <strong>${escapeHtml(intakeTitle(item))}</strong>
      </div>
      ${errorValue ? renderOperationalError(errorValue, { stage: errorStage, compact: true, actionHtml: archived ? "" : errorAction }) : ""}
    </article>
    ${renderIntakeFacts(item)}
    ${renderIntakeSnapshots(item)}
    ${machineCandidates ? `<section class="candidate-stack"><h3>整理结果</h3>${machineCandidates}</section>` : ""}
    ${item.status === "confirmed" ? renderConfirmedRecord(item, final) : ""}
    ${item.rejection_reason ? `<p class="validation-error">不采用原因：${escapeHtml(item.rejection_reason)}</p>` : ""}
    ${recordActions ? `<div class="intake-record-actions"><p>${archived ? "恢复后会重新出现在当前列表；正式档案不会因此改变。" : "删除或移除只会隐藏这条待处理记录，不会改动已经确认的正式档案。"}</p>${recordActions}</div>` : ""}
  `;
}

function renderIntakeFacts(item) {
  const collection = item.collection || item.review?.collection || null;
  const collectionBoundary = {
    queued: "材料占位与处理任务已持久排队，尚未开始抓取；未进入正式档案。",
    confirmed: "已由人工确认并进入正式档案；证据固定到本版本快照。",
    rejected: "已由人工决定不采用，未进入正式档案。",
    cancelled: "已由人工撤销，未进入正式档案。",
    generation_failed: "材料已保存，但候选生成失败；尚未进入正式档案，可重新生成。",
    parsed: "材料已保存，尚未生成可审核候选；未进入正式档案。",
    failed: "本材料处理失败，未进入正式档案。",
  }[item.status] || "系统整理结果尚未确认；确认前不会进入专题成果。";
  return `
    <details class="intake-facts-disclosure">
      <summary>来源、采集与追踪信息</summary>
      <dl class="intake-facts">
      <div><dt>输入类型</dt><dd>${escapeHtml(LABELS.inputType[item.input_type] || item.input_type)}</dd></div>
      <div><dt>来源说明</dt><dd>${escapeHtml(item.source?.description || "未知来源")}</dd></div>
      <div><dt>原始地址</dt><dd>${escapeHtml(item.source?.canonical_url || item.source?.url || "未知地址")}</dd></div>
      <div><dt>标题</dt><dd>${escapeHtml(item.title || "未知标题")}</dd></div>
      <div><dt>发布时间</dt><dd>${formatDate(item.published_at, true)}</dd></div>
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
      </dl>
    </details>`;
}

function renderIntakeSnapshots(item) {
  const raw = item.material?.raw_snapshot || "";
  const extracted = item.material?.extracted_snapshot || "";
  return `
    <details class="snapshot-box" open>
      <summary>提取后的正文</summary>
      <pre>${escapeHtml(extracted)}</pre>
    </details>
    <details class="snapshot-box">
      <summary>原始输入</summary>
      <pre>${escapeHtml(raw)}</pre>
    </details>`;
}

function renderReviewMaterialSummary(item) {
  const sourceName = item.source?.description || item.search?.site || item.search?.channel || "来源未知";
  return `
    <div class="review-material-summary">
      <div><span>来源</span><strong>${escapeHtml(sourceName)}</strong></div>
      <div><span>采集时间</span><strong>${formatDate(item.created_at, true)}</strong></div>
      <div><span>快照</span><strong>${item.material?.extracted_hash ? "已固定" : "尚未固定"}</strong></div>
      <div><span>正式档案</span><strong>尚未改变</strong></div>
    </div>`;
}

function validationErrorLabel(error) {
  const text = String(error || "");
  if (/Event start time must be a valid ISO-8601 datetime/i.test(text)) return "事件时间格式无法识别，请填写日期，例如 2026-08-22；无法确定时请留空。";
  if (/Event time .* earlier than investigation start/i.test(text)) return "该事件发生在专题设定的开始时间之前，不能直接加入本专题；请核对时间、修改专题范围，或不采用这条材料。";
  if (/Event time .* later than investigation end/i.test(text)) return "该事件发生在专题设定的结束时间之后，不能直接加入本专题；请核对时间、修改专题范围，或不采用这条材料。";
  if (/At least one claim/i.test(text)) return "至少保留一条主张候选。";
  if (/At least one evidence/i.test(text)) return "至少纳入一条能够在快照中定位的证据原句。";
  if (/Evidence snippet is missing/i.test(text)) return "这条原文依据没有内容，已默认不采用。";
  if (/Evidence snippet is not an exact substring/i.test(text)) return "这条依据无法在保存的原文中精确找到，已默认不采用。";
  if (/Evidence paragraph_id does not match/i.test(text)) return "原文段落位置不一致，已按保存的原文重新定位。";
  if (/cannot be precisely located/i.test(text)) return "所选证据无法在完整快照中精确定位，请恢复原句或排除它。";
  if (/event title.*required/i.test(text)) return "事件标题不能为空。";
  if (/merge target/i.test(text)) return "所选合并目标无效或不属于当前事件，请重新选择。";
  if (/explicit human change/i.test(text)) return "选择“修改”时，至少需要明确修改一个候选字段。";
  return text;
}

function previewActionLabel(action) {
  return ({ create: "新建", "create-modified": "修改后新建", merge: "合并", exclude: "排除", include: "纳入" })[action] || action || "保持";
}

function renderConfirmationPreview(preview) {
  const formal = preview.semantic_preview || preview.formal || {};
  const event = formal.event || {};
  const source = formal.source || {};
  const document = formal.document || {};
  const snapshot = formal.snapshot || {};
  return `
    <div class="preview-heading">
      <span class="task-stage ${preview.confirmable ? "ready" : "failed"}">${preview.confirmable ? "可以确认" : "需要修正"}</span>
      <div><strong>${preview.confirmable ? "确认后将发生这些变化" : "当前还不能入档"}</strong><p>这是正式区的语义预览，不会在此步骤写入数据。</p></div>
    </div>
    ${preview.errors?.length ? `<ul class="preview-errors">${preview.errors.map((error) => `<li>${escapeHtml(validationErrorLabel(error))}</li>`).join("")}</ul>` : ""}
    <div class="preview-object-grid">
      <section><span>来源与文档</span><strong>${escapeHtml(source.name || "来源未知")}</strong><p>${escapeHtml(document.title || "文档标题未知")} · ${document.published_at ? formatDate(document.published_at, true) : "发布时间未知"}</p></section>
      <section><span>原文快照</span><strong>${Number(snapshot.length || 0).toLocaleString("zh-CN")} 字符</strong><p>已保存，可追溯</p></section>
      <section class="preview-event"><span>事件 · ${escapeHtml(previewActionLabel(event.action))}</span><strong>${escapeHtml(event.title || "事件标题未知")}</strong><p>${escapeHtml(event.summary || "没有事件摘要")}</p><small>${escapeHtml(event.event_type || "类型未知")} · ${event.start_at ? formatEventDate(event.start_at) : "时间未知"} · ${escapeHtml(event.location_name || "地点未知")} · ${escapeHtml(LABELS.importance[event.importance] || event.importance || "重要性未知")}</small></section>
    </div>
    <div class="preview-change-lists">
      <section><h4>实体 ${formal.entities?.length || 0}</h4>${formal.entities?.length ? `<ul>${formal.entities.map((entity) => `<li><span>${escapeHtml(previewActionLabel(entity.action))}</span>${escapeHtml(entity.name || entity.merge_entity_id || "实体未知")}<small>${escapeHtml(entity.entity_type || "类型未知")} · ${escapeHtml(entity.role || "角色未知")}${entity.aliases?.length ? ` · 别名 ${escapeHtml(entity.aliases.join("、"))}` : ""}</small></li>`).join("")}</ul>` : "<p>不创建或合并实体。</p>"}</section>
      <section><h4>关键信息 ${formal.claims?.length || 0}</h4>${formal.claims?.length ? `<ul>${formal.claims.map((claim) => `<li><span>${escapeHtml(previewActionLabel(claim.action))}</span>${escapeHtml(claim.text || claim.merge_claim_id || "信息未知")}<small>${escapeHtml(LABELS.claim[claim.status] || claim.status || "状态未知")}${claim.temporal_scope ? ` · ${escapeHtml(claim.temporal_scope)}` : ""}</small></li>`).join("")}</ul>` : "<p>没有要写入的关键信息。</p>"}</section>
      <section><h4>原文依据 ${formal.evidence?.length || 0}</h4>${formal.evidence?.length ? `<ul>${formal.evidence.map((evidence) => `<li><span>${escapeHtml(LABELS.stance[evidence.stance] || evidence.stance || "背景")}</span><q>${escapeHtml(evidence.snippet || "原文未知")}</q><small>已定位到原文快照</small></li>`).join("")}</ul>` : "<p>没有可定位的原文依据。</p>"}</section>
    </div>
    ${formal.actions?.length ? `<section class="preview-action-summary"><h4>确认采用后将执行</h4><ol>${formal.actions.map((action) => `<li>${escapeHtml(action)}</li>`).join("")}</ol>${formal.candidate_generation?.degraded ? "<p>这份基础草稿已经过当前表单校验，仍以你的本次确认为准。</p>" : ""}</section>` : ""}
    ${formal.scope?.mode ? `<div class="preview-scope-note">对象范围：${formal.scope.mode === "investigation-only" ? "仅限当前专题" : "已显式允许跨专题复用"}</div>` : ""}
    `;
}

function renderConfirmedRecord(item, final) {
  const eventId = final.event || item.final_event_id || item.confirmation_result?.final_event_id || "";
  const snapshotId = final.snapshot || item.final_snapshot_id || item.confirmation_result?.formal_object_ids?.snapshot || "";
  return `
    <section class="confirmed-trace">
      <span class="task-stage ready">已确认入档</span>
      <h3>${escapeHtml(intakeTitle(item))}</h3>
      <p>这条材料已经加入专题成果，并与保存的原文建立关联。</p>
      <div class="trace-links">
        <button class="btn btn-primary" type="button" data-intake-action="open-event" data-event-target="${escapeHtml(eventId)}" ${eventId ? "" : "disabled"}>查看正式事件</button>
        <button class="btn btn-ghost" type="button" data-intake-action="continue-review">继续下一条</button>
      </div>
      ${snapshotId ? `<a href="/snapshots/${escapeHtml(snapshotId)}">查看保存的原文</a>` : ""}
    </section>`;
}

function renderIntakeReview(item) {
  state.intakePreviewApproval = null;
  state.intakePreviewRequestSerial += 1;
  const event = candidateList(item, "event")[0]?.machine?.fields || {};
  const entities = candidateList(item, "entity");
  const claims = candidateList(item, "claim");
  const evidence = candidateList(item, "evidence");
  const confirmationDefaults = candidateConfirmationDefaults(item);
  const includedClaims = claims.filter((candidate) => confirmationDefaults.claimKeys.has(candidate.candidate_key));
  const canDirectConfirm = confirmationDefaults.evidenceKeys.size > 0 && includedClaims.length > 0;
  const eventOptions = state.intakeOptions.events || [];
  const entityOptions = state.intakeOptions.entities || [];
  const claimOptions = state.intakeOptions.claims || [];
  const generation = item.candidate_generation || {};
  const readyItems = state.intakeItems.filter((candidate) => candidate.status === "candidate_ready" && !recordIsArchived(candidate));
  const readyIndex = Math.max(0, readyItems.findIndex((candidate) => candidate.id === item.id));
  const normalizedTitle = String(event.title || "").trim().toLocaleLowerCase();
  const suggestedMerge = normalizedTitle
    ? eventOptions.find((option) => String(option.title || "").trim().toLocaleLowerCase() === normalizedTitle)
    : null;
  const defaultDisposition = suggestedMerge ? "merge" : "create";
  const degraded = ["fallback", "fallback-after-error"].includes(generation.mode);
  const rawEventTime = String(event.start_at || event.event_time || "").trim();
  const normalizedEventTime = normalizeEventTimeForConfirmation(rawEventTime, { strict: false });
  const editableEventTime = eventTimeInputValue(normalizedEventTime);
  const eventTimeNotice = rawEventTime && !normalizedEventTime
    ? '<small class="validation-error">系统无法可靠识别候选时间，已留空；原始表述仍保留在上方原文中。</small>'
    : event.event_time_source_text && (
      event.event_time_basis === "source_partial_date_with_document_year"
      || String(event.event_time_basis || "").startsWith("source_cued_")
    )
      ? `<small>原文写作“${escapeHtml(event.event_time_source_text)}”，系统已按这条原文时间提示整理为日期；加入前可修改。</small>`
      : '<small>填写事件发生日期，例如 2026-08-22；无法确定时留空。</small>';
  const degradedWarning = degraded ? `
    <div class="task-degradation review-degradation" role="status">
      <strong>基础草稿 · 需要逐项核对</strong>
      <span>原文已经保存；这份草稿由基础规则整理，可能不完整。请对照固定快照修改或选择不采用，确认前不会进入正式档案。</span>
      ${generation.error ? `<details><summary>为什么使用基础草稿？</summary><small>${escapeHtml(generation.error)}</small></details>` : ""}
    </div>` : "";
  const excludedCandidateNotice = confirmationDefaults.excludedEvidenceCount || confirmationDefaults.excludedClaimCount
    ? `<div class="task-degradation review-degradation" role="status"><strong>已自动避开无法回到原文的内容</strong><span>${confirmationDefaults.excludedEvidenceCount ? `${confirmationDefaults.excludedEvidenceCount} 条原文依据无法精确定位，默认不采用。` : ""}${confirmationDefaults.excludedClaimCount ? ` ${confirmationDefaults.excludedClaimCount} 条因此缺少依据的关键信息也不会入档。` : ""}${confirmationDefaults.evidenceKeys.size && confirmationDefaults.claimKeys.size ? "其余有效内容仍可直接加入专题。" : "当前没有剩余的有效内容，请修改后再检查，或选择不采用。"}</span></div>`
    : "";
  return `
    <section class="intake-review-material">
      <div class="intake-step-heading"><span>查看原文</span><div><h3>这份资料实际说了什么？</h3><p>采用前先确认关键原句确实存在于固定原文中。</p></div></div>
      ${renderReviewMaterialSummary(item)}
      ${renderIntakeSnapshots(item)}
      ${renderIntakeFacts(item)}
      <div class="intake-mobile-next"><button class="btn btn-primary" type="button" data-intake-step="2">原文已核对，查看候选 →</button></div>
    </section>
    <section class="intake-review-decision">
    <div class="intake-step-heading"><span>作出决定</span><div><h3>把这份整理结果加入专题吗？</h3><p>${readyItems.length ? `本轮第 ${readyIndex + 1} / ${readyItems.length} 条；也可以返回列表多选处理。` : "确认前不会进入专题成果。"}</p></div></div>
    ${degradedWarning}
    ${excludedCandidateNotice}
    <form class="review-form" data-review-form="${escapeHtml(item.id)}">
      <section class="review-candidate-summary">
        <div class="review-candidate-summary-head"><span>${suggestedMerge ? "建议合并" : "建议新建事件"}</span><small>${includedClaims.length} 条关键信息 · ${confirmationDefaults.evidenceKeys.size} 条原文依据将加入</small></div>
        <h3>${escapeHtml(event.title || "事件标题尚未识别")}</h3>
        <p>${escapeHtml(event.summary || "系统没有生成摘要，请查看原文后修改或不采用。")}</p>
        ${includedClaims.length ? `<ul>${includedClaims.slice(0, 3).map((candidate) => `<li>${escapeHtml(candidate.machine?.fields?.text || "未填写关键信息")}</li>`).join("")}</ul>` : '<p class="outcome-gap-note">当前没有带有效原文依据的关键信息，请修改或不采用。</p>'}
        ${suggestedMerge ? `<div class="review-merge-suggestion">发现同名正式事件“${escapeHtml(suggestedMerge.title)}”，默认建议合并；请检查后确认。</div>` : ""}
      </section>
      <details id="intake-editor" class="review-editor-disclosure">
        <summary>修改详细内容或合并目标</summary>
        <div class="review-editor-body">
      <section class="review-section">
        <h3>保存方式</h3>
        <div class="review-grid">
          <label><span>处置方式</span>
            <select id="intake-disposition">
              <option value="create" ${defaultDisposition === "create" ? "selected" : ""}>新建正式事件</option>
              <option value="merge" ${defaultDisposition === "merge" ? "selected" : ""}>合并到既有事件</option>
              <option value="modify">修改后新建事件</option>
            </select>
          </label>
          <label><span>合并目标事件</span>
            <select id="intake-merge-event"><option value="">请选择既有事件</option>${eventOptions.map((option) => `<option value="${escapeHtml(option.id)}" ${suggestedMerge?.id === option.id ? "selected" : ""}>${escapeHtml(option.title)}</option>`).join("")}</select>
          </label>
          <input id="intake-analyst" type="hidden" value="analyst">
        </div>
      </section>
      <section class="review-section">
        <h3>候选事件修改</h3>
        <div class="review-grid">
          <label><span>标题（未知必须由人工补实）</span><input id="intake-event-title" value="${escapeHtml(event.title || "")}" maxlength="500"></label>
          <label><span>事件类型</span><input id="intake-event-type" value="${escapeHtml(event.event_type || "incident")}" maxlength="80"></label>
          <label><span>事件时间（未知留空）</span><input id="intake-event-start" value="${escapeHtml(editableEventTime)}" placeholder="例如 2026-08-22">${eventTimeNotice}</label>
          <label><span>地点（未知留空）</span><input id="intake-event-location" value="${escapeHtml(event.location_name || "")}" maxlength="200"></label>
          <label><span>重要性</span><select id="intake-event-importance"><option value="medium" ${event.importance === "medium" || !event.importance ? "selected" : ""}>中</option><option value="high" ${event.importance === "high" ? "selected" : ""}>高</option><option value="critical" ${event.importance === "critical" ? "selected" : ""}>极高</option><option value="low" ${event.importance === "low" ? "selected" : ""}>低</option></select></label>
        </div>
        <label><span>摘要</span><textarea id="intake-event-summary" rows="4">${escapeHtml(event.summary || "")}</textarea></label>
      </section>
      ${entities.length ? `<section class="review-section"><h3>候选实体</h3>${entities.map((candidate) => {
        const fields = candidate.machine?.fields || {};
        const aliases = Array.isArray(fields.aliases) ? fields.aliases.join(", ") : (fields.aliases || "");
        return `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <div class="review-grid">
            <label><span>名称</span><input data-entity-field="name" value="${escapeHtml(fields.name || "")}"></label>
            <label><span>类型</span><input data-entity-field="entity_type" value="${escapeHtml(fields.entity_type || "organization")}"></label>
            <label><span>别名（逗号分隔）</span><input data-entity-field="aliases" value="${escapeHtml(aliases)}"></label>
            <label><span>角色</span><input data-entity-field="role" value="${escapeHtml(fields.role || "related")}"></label>
            <label><span>处置</span><select data-entity-field="action"><option value="create">新建</option><option value="merge">合并</option><option value="exclude">排除</option></select></label>
            <label><span>合并目标实体</span><select data-entity-field="merge_entity_id"><option value="">请选择实体</option>${entityOptions.map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.name)}</option>`).join("")}</select></label>
          </div>
        </div>`;
      }).join("")}</section>` : ""}
      <section class="review-section"><h3>关键信息</h3>${claims.map((candidate) => {
        const fields = candidate.machine?.fields || {};
        const confidence = unitIntervalValue(fields.confidence, 0.5);
        const included = confirmationDefaults.claimKeys.has(candidate.candidate_key);
        const status = fields.status || (confirmationDefaults.supportedClaimKeys.has(candidate.candidate_key) ? "supported" : "unverified");
        return `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <label><span>简明说法</span><textarea data-claim-field="text" rows="3">${escapeHtml(fields.text || "")}</textarea></label>
          <div class="review-grid">
            <label><span>原文关系</span><select data-claim-field="status"><option value="unverified" ${status === "unverified" ? "selected" : ""}>尚无直接支持</option><option value="supported" ${status === "supported" ? "selected" : ""}>原文支持</option><option value="contested" ${status === "contested" ? "selected" : ""}>原文有冲突</option><option value="confirmed" ${status === "confirmed" ? "selected" : ""}>人工确认</option><option value="refuted" ${status === "refuted" ? "selected" : ""}>已有反证</option></select></label>
            <label><span>置信度（0–1）</span><input type="number" min="0" max="1" step="0.05" data-claim-field="confidence" value="${escapeHtml(confidence)}"></label>
            <label><span>时间范围</span><input data-claim-field="temporal_scope" value="${escapeHtml(fields.temporal_scope || "")}" maxlength="120"></label>
            <label><span>处置</span><select data-claim-field="action"><option value="create" ${included ? "selected" : ""}>新建</option><option value="merge">合并</option><option value="exclude" ${included ? "" : "selected"}>排除</option></select></label>
            <label><span>合并目标主张</span><select data-claim-field="merge_claim_id"><option value="">请选择主张</option>${claimOptions.map((option) => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.text || option.title || option.id)}</option>`).join("")}</select></label>
          </div>
        </div>`;
      }).join("") || '<p class="muted">机器未提出主张候选；未知保持未知。</p>'}</section>
      <section class="review-section"><h3>原文依据（必须能在原文中找到）</h3>${evidence.map((candidate) => {
        const fields = candidate.machine?.fields || {};
        const strength = unitIntervalValue(fields.strength, 0.7);
        const stance = fields.stance || "context";
        const included = confirmationDefaults.evidenceKeys.has(candidate.candidate_key);
        return `
        <div class="candidate-editor" data-candidate="${escapeHtml(candidate.candidate_key)}">
          <label><span>原文片段</span><textarea data-evidence-field="snippet" rows="3">${escapeHtml(fields.snippet || "")}</textarea></label>
          <div class="review-grid">
            <label><span>立场</span><select data-evidence-field="stance"><option value="context" ${stance === "context" ? "selected" : ""}>背景</option><option value="supports" ${stance === "supports" ? "selected" : ""}>支持</option><option value="contradicts" ${stance === "contradicts" ? "selected" : ""}>冲突</option></select></label>
            <label><span>证据强度（0–1）</span><input type="number" min="0" max="1" step="0.05" data-evidence-field="strength" value="${escapeHtml(strength)}"></label>
            <label><span>人工备注</span><input data-evidence-field="note" value="${escapeHtml(fields.note || "")}" maxlength="1000"></label>
            <label><span>处置</span><select data-evidence-field="action"><option value="include" ${included ? "selected" : ""}>纳入</option><option value="exclude" ${included ? "" : "selected"}>排除</option></select></label>
          </div>
          ${candidate.validation_error ? `<p class="validation-error">${escapeHtml(validationErrorLabel(candidate.validation_error))}</p>` : `<p class="validation-ok">已在保存的原文中找到</p>`}
        </div>`;
      }).join("")}</section>
      <div class="review-editor-apply"><button class="btn btn-primary" type="button" data-intake-action="preview">检查修改后的内容</button></div>
        </div>
      </details>
      <section id="intake-reject-panel" class="review-section review-reject-section" hidden>
        <div class="review-reject-heading"><div><h3>忽略这份整理结果</h3><p>原始材料仍会保留，但不会进入专题成果。</p></div><button class="text-btn" type="button" data-intake-action="cancel-reject">取消</button></div>
        <label><span>简单说明原因</span><textarea id="intake-reject-reason" rows="2" placeholder="例如：与本专题无关、内容重复或来源无法核实"></textarea></label>
      </section>
      <div id="intake-preview" class="intake-preview" aria-live="polite"><p class="muted">选择“加入专题”后，系统会先校验内容；只有合并或修改时需要额外确认。</p></div>
      <div class="review-actions">
        <p class="review-later-note">关闭窗口即可稍后处理；未确认内容不会进入专题成果。</p>
        <button class="btn btn-ghost" type="button" data-intake-action="reject-toggle" aria-expanded="false" aria-controls="intake-reject-panel">忽略</button>
        <button class="btn btn-ghost" type="button" data-intake-action="modify">修改</button>
        <button class="btn btn-primary" type="button" data-intake-action="accept" ${canDirectConfirm ? "" : "disabled"} title="${canDirectConfirm ? "校验通过后直接加入专题" : "没有可定位的原文依据，请先修改"}">加入专题</button>
        <button class="btn btn-primary" type="button" data-intake-action="confirm" hidden disabled title="请先检查采用后的变化">确认采用</button>
      </div>
    </form>
    </section>`;
}
function intakeReviewRoute(itemId, action) {
  if (state.intakeScopeInvestigationId) {
    return `/pldr-api/v1/investigations/${encodeURIComponent(state.intakeScopeInvestigationId)}/intake/${encodeURIComponent(itemId)}/${action}`;
  }
  return `/pldr-api/v1/intake/${encodeURIComponent(itemId)}/${action}`;
}

async function loadIntakeDetail(
  itemId,
  requestContext = {
    serial: state.intakeRequestSerial,
    scopeInvestigationId: state.intakeScopeInvestigationId,
  },
) {
  if (!itemId) return null;
  const existing = state.intakeItems.find((item) => item.id === itemId);
  if (existing?.material && Array.isArray(existing.candidates)) return existing;
  const route = requestContext.scopeInvestigationId
    ? `/pldr-api/v1/investigations/${encodeURIComponent(requestContext.scopeInvestigationId)}/intake/${encodeURIComponent(itemId)}?visibility=all`
    : `/pldr-api/v1/intake/${encodeURIComponent(itemId)}?visibility=all`;
  const detail = await api(route);
  if (
    requestContext.serial !== state.intakeRequestSerial
    || requestContext.scopeInvestigationId !== state.intakeScopeInvestigationId
  ) return null;
  const mergedDetail = {
    ...existing,
    ...detail,
    allowed_actions: requestContext.scopeInvestigationId
      ? (existing?.allowed_actions ?? [])
      : (detail.allowed_actions !== undefined ? detail.allowed_actions : existing?.allowed_actions),
  };
  state.intakeItems = state.intakeItems.map((item) => item.id === itemId ? mergedDetail : item);
  if (!requestContext.scopeInvestigationId && state.intakeVisibility === "active") {
    state.globalIntakeItems = state.globalIntakeItems.map((item) => item.id === itemId ? mergedDetail : item);
  }
  return mergedDetail;
}

async function refreshIntakeData(preferredItemId = state.selectedIntakeId) {
  const requestContext = {
    serial: ++state.intakeRequestSerial,
    scopeInvestigationId: state.intakeScopeInvestigationId,
  };
  const requestIsStale = () => requestContext.serial !== state.intakeRequestSerial
    || requestContext.scopeInvestigationId !== state.intakeScopeInvestigationId;
  let preferredError = null;
  if (requestContext.scopeInvestigationId) {
    const investigationId = requestContext.scopeInvestigationId;
    const visibility = state.intakeVisibility === "archived" ? "removed" : "active";
    const [tasks, options] = await Promise.all([
      loadAllInvestigationTasks(investigationId, visibility),
      state.intakeVisibility === "active"
        ? api(`/pldr-api/v1/investigations/${encodeURIComponent(investigationId)}/review-options`)
        : Promise.resolve({ events: [], entities: [], claims: [] }),
    ]);
    if (requestContext.serial !== state.intakeRequestSerial || investigationId !== state.intakeScopeInvestigationId) return { found: false, stale: true };
    if (state.intakeVisibility === "active") state.investigationTasks.set(investigationId, tasks);
    const byId = new Map();
    const pendingTasks = tasks.filter((task) => (
      taskBelongsInPending(task) || taskIntakeId(task) === preferredItemId
    ));
    pendingTasks.forEach((task) => {
      if (task.intake_item?.id) {
        byId.set(task.intake_item.id, {
          ...task.intake_item,
          allowed_actions: task.allowed_actions ?? [],
          archived: task.intake_item.archived ?? task.archived,
          archived_at: task.intake_item.archived_at || task.archived_at || task.removed_at,
          removed_at: task.intake_item.removed_at || task.removed_at,
        });
      }
    });
    state.intakeItems = [...byId.values()].filter((item) => state.intakeVisibility !== "active" || ACTIVE_INTAKE_STATUSES.has(item.status));
    state.intakeOptions = options || { events: [], entities: [], claims: [] };
  } else {
    const [list, options] = await Promise.all([
      api(`/pldr-api/v1/intake?limit=${GLOBAL_INTAKE_LOAD_LIMIT}&include_detail=false&visibility=${encodeURIComponent(state.intakeVisibility)}`),
      state.intakeVisibility === "active" ? api("/pldr-api/v1/intake/options") : Promise.resolve({ events: [], entities: [], claims: [] }),
    ]);
    if (requestContext.serial !== state.intakeRequestSerial || state.intakeScopeInvestigationId) return { found: false, stale: true };
    state.intakeItems = (list.items || []).filter((item) => state.intakeVisibility !== "active" || ACTIVE_INTAKE_STATUSES.has(item.status));
    if (state.intakeVisibility === "active") state.globalIntakeItems = [...state.intakeItems];
    state.intakeOptions = options || { events: [], entities: [], claims: [] };
  }
  if (state.intakeVisibility === "active" && !state.intakeScopeInvestigationId && preferredItemId && !state.intakeItems.some((item) => item.id === preferredItemId)) {
    try {
      const olderItem = await api(`/pldr-api/v1/intake/${encodeURIComponent(preferredItemId)}`);
      if (requestContext.serial !== state.intakeRequestSerial || state.intakeScopeInvestigationId) return { found: false, stale: true };
      if (ACTIVE_INTAKE_STATUSES.has(olderItem.status)) state.intakeItems = [olderItem, ...state.intakeItems];
    } catch (error) {
      if (requestIsStale()) return { found: false, stale: true };
      preferredError = error;
    }
  }
  const target = (preferredItemId && state.intakeItems.some((item) => item.id === preferredItemId)
    ? preferredItemId
    : null)
    || (state.intakeVisibility === "active" ? state.intakeItems.find((item) => item.status === "candidate_ready")?.id : null)
    || state.intakeItems[0]?.id
    || null;
  if (target) {
    try {
      await loadIntakeDetail(target, requestContext);
      if (
        requestContext.serial !== state.intakeRequestSerial
        || requestContext.scopeInvestigationId !== state.intakeScopeInvestigationId
      ) return { found: false, stale: true };
    } catch (error) {
      if (requestIsStale()) return { found: false, stale: true };
      preferredError = error;
    }
  }
  state.selectedIntakeId = target;
  renderIntakeList();
  if (preferredError) {
    $("#intake-detail").innerHTML = `${renderOperationalError(preferredError, { stage: "fetch" })}<button class="btn btn-ghost" type="button" data-intake-action="retry-detail">重新读取这条材料</button>`;
  } else {
    renderIntakeDetail(state.intakeItems.find((item) => item.id === target) || null);
  }
  return { found: !preferredItemId || target === preferredItemId, error: preferredError };
}

async function setIntakeVisibility(visibility) {
  if (!new Set(["active", "archived"]).has(visibility) || state.intakeVisibility === visibility) return;
  state.intakeVisibility = visibility;
  state.selectedIntakeId = null;
  invalidateIntakePreview();
  $("#intake-detail").innerHTML = `<div class="panel-empty">正在读取${visibility === "archived" ? "已删除" : "当前"}材料…</div>`;
  try {
    await refreshIntakeData(null);
    setIntakeMobileStep(0);
  } catch (error) {
    $("#intake-detail").innerHTML = renderOperationalError(error, { stage: "fetch" });
    toast(`材料列表读取失败：${error.message || "未知错误"}`, "error", 7000);
  }
}

function setIntakeMobileStep(step) {
  state.intakeMobileStep = clamp(Number(step) || 0, 0, 2);
  const modal = $("#intake-modal");
  if (modal) modal.dataset.intakeStep = String(state.intakeMobileStep);
  $$('[data-intake-step]', $("#intake-mobile-steps")).forEach((button) => {
    const active = Number(button.dataset.intakeStep) === state.intakeMobileStep;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "step" : "false");
  });
}

async function openIntakeModal(itemId = null, quiet = false, scopeInvestigationId = null) {
  const modal = $("#intake-modal");
  state.intakeRequestSerial += 1;
  state.intakeActionSerial += 1;
  setIntakeActionBusy(false);
  invalidateIntakePreview();
  state.selectedIntakeIds.clear();
  state.intakeScopeInvestigationId = scopeInvestigationId;
  state.intakeScopeInvestigationTitle = state.investigations.find((item) => item.id === scopeInvestigationId)?.title || null;
  const modalTitle = state.intakeScopeInvestigationTitle
    ? `待确认 · ${state.intakeScopeInvestigationTitle}`
    : "待确认";
  $("#intake-modal-title").textContent = modalTitle;
  $("#intake-modal-title").title = modalTitle;
  state.intakeVisibility = "active";
  setIntakeMobileStep(itemId ? 1 : 0);
  if (!quiet && typeof modal.showModal === "function") modal.showModal();
  else if (!quiet) modal.setAttribute("open", "");
  const preferred = itemId || state.selectedIntakeId;
  let result;
  const refreshPromise = refreshIntakeData(preferred);
  const requestSerial = state.intakeRequestSerial;
  try {
    result = await refreshPromise;
  } catch (error) {
    if (state.intakeScopeInvestigationId !== scopeInvestigationId || state.intakeRequestSerial !== requestSerial) return;
    $("#intake-detail").innerHTML = `${renderOperationalError(error, {
      stage: "fetch",
      actionHtml: '<button class="btn btn-ghost" type="button" data-intake-action="retry-intake">重新读取采集箱</button>',
    })}`;
    toast(`采集箱读取失败：${error.message || "未知错误"}`, "error", 7000);
    return;
  }
  if (!result.stale && result.error) {
    toast(`材料读取失败：${result.error.message || "未知错误"}`, "error", 7000);
  } else if (itemId && !result.found && !result.stale) {
    toast(`指定版本无法打开：${result.error?.message || "材料不存在"}`, "error", 7000);
  }
}

function closeIntakeModal() {
  const modal = $("#intake-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
  state.intakeRequestSerial += 1;
  state.intakeActionSerial += 1;
  setIntakeActionBusy(false);
  invalidateIntakePreview();
  state.selectedIntakeIds.clear();
  state.intakeScopeInvestigationId = null;
  state.intakeScopeInvestigationTitle = null;
  $("#intake-modal-title").textContent = "待确认";
  $("#intake-modal-title").removeAttribute("title");
  state.intakeVisibility = "active";
  if (state.globalIntakeItems.length) {
    state.intakeItems = [...state.globalIntakeItems];
    state.selectedIntakeId = state.intakeItems.find((item) => item.id === state.selectedIntakeId)?.id || state.intakeItems[0]?.id || null;
    renderIntakeList();
  }
}

function selectedIntakeItem() {
  return state.intakeItems.find((item) => item.id === state.selectedIntakeId) || null;
}

async function updateIntakeRecordVisibility(item, action) {
  if (!intakeRecordActionAllowed(item, action)) {
    toast("当前状态不允许执行这项删除或恢复操作，请刷新后重试。", "error", 6000);
    return;
  }
  const actionScopeId = state.intakeScopeInvestigationId;
  const actionScopeTitle = state.intakeScopeInvestigationTitle;
  const scoped = Boolean(actionScopeId);
  const copy = action === "archive-record"
    ? {
      question: `删除“${intakeTitle(item)}”？\n\n它会移到“已删除”，之后可以恢复。已经保存的材料、快照和正式档案不会改变。`,
      route: API_ROUTES.intakeAction(item.id, "archive"),
      success: "材料已移到“已删除”；正式档案没有改变。",
      }
    : action === "remove-record"
      ? {
        question: `从“${actionScopeTitle || "当前专题"}”移除“${intakeTitle(item)}”？\n\n它会移到本专题“已删除”，之后可以恢复。全局材料和正式档案不会改变。`,
        route: API_ROUTES.investigationIntakeAction(actionScopeId, item.id, "remove"),
        success: "材料已从本专题移除；全局材料和正式档案没有改变。",
      }
      : {
        question: `恢复“${intakeTitle(item)}”？\n\n恢复只会让这条记录重新出现在${scoped ? `专题“${actionScopeTitle || "当前专题"}”` : "采集箱"}，不会改变正式档案。`,
        route: scoped
          ? API_ROUTES.investigationIntakeAction(actionScopeId, item.id, "restore")
          : API_ROUTES.intakeAction(item.id, "restore"),
        success: `材料已恢复到${scoped ? `专题“${actionScopeTitle || "当前专题"}”` : "采集箱"}。`,
      };
  if (!window.confirm(copy.question)) return;
  setIntakeActionBusy(true);
  try {
    await api(copy.route, { method: "POST" });
    invalidateIntakePreview();
    state.selectedIntakeId = null;
    await refreshIntakeData(null);
    if (actionScopeId) await loadInvestigationWorkspace(actionScopeId, { quiet: true });
    else if (state.activeInvestigationId) await loadInvestigationWorkspace(state.activeInvestigationId, { quiet: true });
    await refreshInvestigationDirectory();
    const shouldCloseAfterRemoval = action !== "restore-record"
      && state.intakeVisibility === "active"
      && state.intakeItems.length === 0;
    toast(copy.success, "success", 5600);
    if (shouldCloseAfterRemoval) closeIntakeModal();
  } catch (error) {
    toast(`${action === "restore-record" ? "恢复" : "删除"}失败：${error.message || "未知错误"}`, "error", 7000);
  } finally {
    setIntakeActionBusy(false);
  }
}

function confirmationFingerprint(payload) {
  return JSON.stringify(payload);
}

function setIntakeConfirmEnabled(enabled) {
  const modal = $("#intake-modal") || document;
  const confirmButton = $('[data-intake-action="confirm"]', modal);
  const previewButton = $('[data-intake-action="preview"]', modal);
  const acceptButton = $('[data-intake-action="accept"]', modal);
  if (confirmButton) {
    confirmButton.hidden = !enabled;
    confirmButton.disabled = state.intakeActionBusy || !enabled;
    confirmButton.textContent = "确认采用";
    confirmButton.title = enabled ? "确认后才会写入正式档案和专题成果" : "请先检查采用后的变化";
  }
  if (previewButton) {
    previewButton.hidden = Boolean(enabled);
    previewButton.disabled = state.intakeActionBusy;
  }
  if (acceptButton) {
    acceptButton.hidden = Boolean(enabled);
    acceptButton.disabled = state.intakeActionBusy;
  }
}

function setIntakeRejectExpanded(expanded) {
  const panel = $("#intake-reject-panel");
  const button = $('[data-intake-action="reject-toggle"]', $("#intake-modal") || document);
  if (!panel || !button) return;
  panel.hidden = !expanded;
  button.textContent = expanded ? "确认不采用" : "不采用";
  button.classList.toggle("btn-danger", expanded);
  button.classList.toggle("btn-ghost", !expanded);
  button.setAttribute("aria-expanded", String(expanded));
  if (expanded) window.setTimeout(() => $("#intake-reject-reason")?.focus(), 0);
  else window.setTimeout(() => button.focus(), 0);
}

function setIntakeActionBusy(busy) {
  state.intakeActionBusy = busy;
  $$('[data-intake-action="preview"], [data-intake-action="accept"], [data-intake-action="modify"], [data-intake-action="reject-toggle"], [data-intake-action="cancel-reject"], [data-intake-action$="-record"]', $("#intake-modal") || document)
    .forEach((button) => { button.disabled = busy; });
  setIntakeConfirmEnabled(Boolean(state.intakePreviewApproval));
}

function invalidateIntakePreview(message = "") {
  state.intakePreviewRequestSerial += 1;
  state.intakePreviewApproval = null;
  setIntakeConfirmEnabled(false);
  const root = $("#intake-preview");
  if (root && message) {
    root.className = "intake-preview";
    root.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
  }
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
      aliases: (fields.aliases || "").split(",").map((alias) => alias.trim()).filter(Boolean).slice(0, 20),
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
      event_type: value("#intake-event-type") || "incident",
      start_at: normalizeEventTimeForConfirmation(value("#intake-event-start")),
      location_name: value("#intake-event-location"),
      importance: value("#intake-event-importance") || "medium",
    },
    entities,
    claims: [...claimGroups.entries()].map(([candidate_key, fields]) => ({
      candidate_key,
      action: fields.action || "create",
      text: fields.text || "",
      status: fields.status || "unverified",
      confidence: unitIntervalValue(fields.confidence, 0.5),
      temporal_scope: fields.temporal_scope || "",
      merge_claim_id: fields.merge_claim_id || null,
    })),
    evidence: [...evidenceGroups.entries()].map(([candidate_key, fields]) => ({
      candidate_key,
      action: fields.action || "include",
      snippet: fields.snippet || "",
      stance: fields.stance || "context",
      strength: unitIntervalValue(fields.strength, 0.7),
      note: fields.note || "",
    })),
  };
}

async function continueInvestigationReview(completedIntakeId) {
  const scopeInvestigationId = state.intakeScopeInvestigationId;
  const modal = $("#intake-modal");
  const refreshPromise = refreshIntakeData(null);
  const requestSerial = state.intakeRequestSerial;
  let refreshResult;
  try {
    refreshResult = await refreshPromise;
  } catch (error) {
    if (
      requestSerial !== state.intakeRequestSerial
      || scopeInvestigationId !== state.intakeScopeInvestigationId
      || !modal.open
    ) return;
    toast(`下一条材料读取失败：${error.message || "未知错误"}`, "error", 7000);
    return;
  }
  if (
    refreshResult?.stale
    || requestSerial !== state.intakeRequestSerial
    || scopeInvestigationId !== state.intakeScopeInvestigationId
    || !modal.open
  ) return;
  const next = selectedIntakeItem();
  if (next?.status === "candidate_ready" && next.id !== completedIntakeId) {
    setIntakeMobileStep(1);
    toast(scopeInvestigationId ? "已打开本专题的下一条待核对材料。" : "已打开采集箱中的下一条待核对材料。", "success", 4200);
    return;
  }
  setIntakeMobileStep(0);
  toast(scopeInvestigationId
    ? "本专题当前没有下一条待核对材料，已返回材料列表。"
    : "采集箱当前没有下一条待核对材料，已返回材料列表。", "info", 5200);
}

async function handleIntakeAction(action, domEvent = null) {
  if (action === "retry-intake") {
    try {
      await refreshIntakeData(state.selectedIntakeId);
    } catch (error) {
      $("#intake-detail").innerHTML = renderOperationalError(error, {
        stage: "fetch",
        actionHtml: '<button class="btn btn-ghost" type="button" data-intake-action="retry-intake">重新读取采集箱</button>',
      });
      toast(`采集箱读取失败：${error.message || "未知错误"}`, "error", 7000);
    }
    return;
  }
  const item = selectedIntakeItem();
  if (!item) return;
  if (action === "modify") {
    const editor = $("#intake-editor");
    if (editor) {
      editor.open = true;
      const disposition = $("#intake-disposition");
      if (disposition?.value === "create") disposition.value = "modify";
      invalidateIntakePreview("修改完成后，点击“检查修改后的内容”。");
      editor.scrollIntoView({ behavior: "smooth", block: "start" });
      window.setTimeout(() => $("#intake-event-title")?.focus(), 250);
    }
    return;
  }
  const directAccept = action === "accept";
  if (directAccept) action = "preview";
  if (action === "cancel-reject") {
    setIntakeRejectExpanded(false);
    return;
  }
  if (action === "reject-toggle") {
    if ($("#intake-reject-panel")?.hidden) {
      setIntakeRejectExpanded(true);
      return;
    }
    action = "reject";
  }
  if (["archive-record", "remove-record", "restore-record"].includes(action)) {
    await updateIntakeRecordVisibility(item, action);
    return;
  }
  if (action === "retry-detail") {
    const requestContext = {
      serial: ++state.intakeRequestSerial,
      scopeInvestigationId: state.intakeScopeInvestigationId,
    };
    $("#intake-detail").innerHTML = '<div class="panel-empty">正在重新读取原始材料、固定快照与候选…</div>';
    try {
      await loadIntakeDetail(item.id, requestContext);
      if (requestContext.serial !== state.intakeRequestSerial) return;
      renderIntakeDetail(selectedIntakeItem());
    } catch (error) {
      if (requestContext.serial !== state.intakeRequestSerial) return;
      $("#intake-detail").innerHTML = `${renderOperationalError(error, { stage: "fetch" })}<button class="btn btn-ghost" type="button" data-intake-action="retry-detail">重新读取这条材料</button>`;
    }
    return;
  }
  if (action === "open-event") {
    const eventId = domEvent?.target?.closest?.("[data-event-target]")?.dataset?.eventTarget || domEvent?.target?.dataset?.eventTarget;
    const investigationId = state.intakeScopeInvestigationId;
    closeIntakeModal();
    await openInvestigationEvent(eventId, investigationId);
    return;
  }
  if (action === "continue-review") {
    await continueInvestigationReview(item.id);
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
  if (state.intakeActionBusy) return;
  const actionSerial = ++state.intakeActionSerial;
  const actionScope = state.intakeScopeInvestigationId;
  const actionIsCurrent = () => actionSerial === state.intakeActionSerial
    && state.selectedIntakeId === item.id
    && state.intakeScopeInvestigationId === actionScope;
  setIntakeActionBusy(true);
  let dispositionCommitted = false;
  try {
    if (action === "preview") {
      const payload = buildConfirmation(item);
      const fingerprint = confirmationFingerprint(payload);
      const previewSerial = ++state.intakePreviewRequestSerial;
      state.intakePreviewApproval = null;
      setIntakeConfirmEnabled(false);
      const pendingRoot = $("#intake-preview");
      pendingRoot.className = "intake-preview";
      pendingRoot.innerHTML = '<p class="muted">正在校验正式区变化…</p>';
      const preview = await api(intakeReviewRoute(item.id, "preview"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (previewSerial !== state.intakePreviewRequestSerial || state.selectedIntakeId !== item.id) return;
      const root = $("#intake-preview");
      root.className = `intake-preview ${preview.confirmable ? "ok" : "error"}`;
      root.innerHTML = renderConfirmationPreview(preview);
      const unchanged = confirmationFingerprint(buildConfirmation(item)) === fingerprint;
      if (preview.confirmable && unchanged) {
        state.intakePreviewApproval = { itemId: item.id, fingerprint };
        setIntakeConfirmEnabled(true);
        if (directAccept && payload.disposition !== "merge") {
          window.setTimeout(() => handleIntakeAction("confirm"), 0);
        } else if (directAccept && payload.disposition === "merge") {
          toast("发现同名事件。请检查合并内容后，再点一次“确认采用”。", "info", 6200);
        }
      } else {
        state.intakePreviewApproval = null;
        setIntakeConfirmEnabled(false);
        if (preview.confirmable && !unchanged) {
          root.insertAdjacentHTML("afterbegin", '<p class="validation-error">预览期间字段发生了变化，请重新预览。</p>');
        }
      }
      root.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    if (action === "confirm") {
      const workspaceId = state.intakeScopeInvestigationId
        || (isServerInvestigation(activeInvestigation()) ? state.activeInvestigationId : null);
      const payload = buildConfirmation(item);
      const fingerprint = confirmationFingerprint(payload);
      if (state.intakePreviewApproval?.itemId !== item.id || state.intakePreviewApproval?.fingerprint !== fingerprint) {
        invalidateIntakePreview("采用内容尚未检查，或检查后字段已变化。请重新检查再确认。");
        throw new Error("请先检查采用后的变化，再确认采用。");
      }
      const result = await api(intakeReviewRoute(item.id, "confirm"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dispositionCommitted = true;
      if (actionIsCurrent()) invalidateIntakePreview();
      const finalEventId = result.final_event_id || result.result?.final_event_id || result.result?.formal_object_ids?.event;
      if (finalEventId) {
        invalidateInvestigationEvidence([finalEventId]);
      }
      toast("确认保存完成，正式事件与固定快照已经建立回链。", "success", 6200);
      await refreshData({ keepSelection: false, quiet: true, preferredEventId: finalEventId });
      await refreshInvestigationDirectory();
      if (workspaceId && state.activeInvestigationId === workspaceId) {
        await loadInvestigationWorkspace(workspaceId, { quiet: true });
        if (investigationNeedsEvidence()) await loadInvestigationEvidence(activeInvestigation());
      }
      if (!actionIsCurrent()) return;
      await refreshIntakeData(null);
      setIntakeMobileStep(selectedIntakeItem() ? 1 : 0);
      return;
    }
    if (action === "reject") {
      const reason = $("#intake-reject-reason")?.value.trim();
      if (!reason) throw new Error("请填写不采用的原因。");
      await api(`/pldr-api/v1/intake/${item.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason }),
      });
      dispositionCommitted = true;
      toast("这份草稿已标记为不采用，未写入正式档案。", "success");
    } else if (action === "cancel") {
      await api(`/pldr-api/v1/intake/${item.id}/cancel`, {
        method: "POST",
        body: JSON.stringify({ analyst: $("#intake-analyst")?.value.trim() || "analyst", reason: "Analyst cancelled before confirmation" }),
      });
      dispositionCommitted = true;
      toast("处理已撤销，未写入正式区。", "success");
    }
    await refreshData({ keepSelection: true, quiet: true });
    await refreshInvestigationDirectory();
    if (actionScope && state.activeInvestigationId === actionScope) {
      await loadInvestigationWorkspace(actionScope, { quiet: true });
    }
    if (!actionIsCurrent()) return;
    await refreshIntakeData(null);
    setIntakeMobileStep(selectedIntakeItem() ? 1 : 0);
  } catch (error) {
    if (action === "preview" && actionIsCurrent()) {
      const root = $("#intake-preview");
      if (root) {
        root.className = "intake-preview error";
        root.innerHTML = renderOperationalError(error, { stage: "validate" });
      }
    } else if (action === "confirm" && !dispositionCommitted && actionIsCurrent()) {
      invalidateIntakePreview("确认没有完成；正式区可能已变化，请重新预览后再确认。");
    }
    toast(dispositionCommitted ? `人工处置已由服务端完成，但页面刷新失败：${error.message}` : `采集箱操作失败：${error.message}`, "error", 7000);
  } finally {
    if (actionSerial === state.intakeActionSerial) setIntakeActionBusy(false);
  }
}

function collectionMetrics() {
  return state.collectionSummary?.metrics || state.collectionSummary || {};
}

function collectionVisibleTargets() {
  if (!state.collectionScopeInvestigationId) return state.collectionTargets;
  const allowedIds = state.collectionScopeTargetIds;
  if (!(allowedIds instanceof Set)) return [];
  return state.collectionTargets.filter((target) => allowedIds.has(target.id));
}

function collectionTargetIsInScope(targetId) {
  return Boolean(targetId) && (
    !state.collectionScopeInvestigationId
    || state.collectionScopeTargetIds?.has(targetId)
  );
}

function collectionIntervalMinutes(target) {
  if (target.interval_minutes != null) return Number(target.interval_minutes);
  if (target.interval_seconds != null) return Math.max(1, Math.round(Number(target.interval_seconds) / 60));
  return null;
}

function collectionTargetCount(target) {
  if ((target?.target_type || "web_page") === "rss_feed") {
    return `${target.discovered_item_count ?? 0} 条`;
  }
  return `V${target.version_count ?? 0}`;
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
  if (state.collectionScopeInvestigationId) {
    const visibleTargets = collectionVisibleTargets();
    const statuses = visibleTargets.map(collectionTargetStatus);
    const cards = [
      [visibleTargets.length, "专题来源"],
      [statuses.filter((status) => status === "healthy").length, "运行正常"],
      [statuses.filter((status) => ["error", "degraded", "stale"].includes(status)).length, "需要恢复"],
      [statuses.filter((status) => ["pending", "new"].includes(status)).length, "待首次运行"],
      ["逐项查看", "版本与队列"],
    ];
    $("#collection-summary").innerHTML = cards.map(([value, label]) => `
      <div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>
    `).join("");
    return;
  }
  const metrics = collectionMetrics();
  const targets = metrics.targets || {};
  const runs = metrics.runs || {};
  const cards = [
    [targets.total ?? metrics.total_targets ?? state.collectionTargets.length, "可靠来源"],
    [targets.healthy ?? metrics.healthy ?? 0, "运行正常"],
    [metrics.changed_pending ?? metrics.pending_changes ?? metrics.pending_review ?? 0, "材料待审"],
    [(targets.degraded ?? 0) + (targets.error ?? metrics.error ?? 0) + (targets.stale ?? 0), "需要恢复"],
    [(runs.queued ?? metrics.queued ?? 0) + (runs.running ?? metrics.running ?? 0), "队列中"],
  ];
  $("#collection-summary").innerHTML = cards.map(([value, label]) => `
    <div><strong>${escapeHtml(value ?? 0)}</strong><span>${escapeHtml(label)}</span></div>
  `).join("");
}

function renderCollectionTargets() {
  const root = $("#collection-target-list");
  const visibleTargets = collectionVisibleTargets();
  if (!visibleTargets.length) {
    root.innerHTML = `
      <div class="collection-empty">
        <strong>${state.collectionScopeInvestigationId ? "本专题还没有可靠来源" : "还没有可靠来源"}</strong>
        <p>在上方添加一个无需登录的公共网页或 RSS。${state.collectionScopeInvestigationId ? "这里不会显示其他专题或全局来源。" : "PLDR 不会用演示运行记录填充这里。"}</p>
      </div>`;
    return;
  }
  root.innerHTML = visibleTargets.map((target) => {
    const status = collectionTargetStatus(target);
    const active = target.id === state.selectedCollectionTargetId;
    const targetType = target.target_type || "web_page";
    const count = collectionTargetCount(target);
    return `
      <div role="listitem">
        <button class="collection-target ${active ? "active" : ""}" type="button" data-collection-target="${escapeHtml(target.id)}">
          <span class="collection-health ${escapeHtml(status)}"></span>
          <span class="collection-target-copy">
            <strong>${escapeHtml(target.name || "未命名来源")}</strong>
            <small>${escapeHtml(target.url || target.canonical_url || "地址未知")}</small>
            <em>${escapeHtml(LABELS.collectionTargetType[targetType] || targetType)} · ${escapeHtml(LABELS.collectionStatus[status] || status)} · ${escapeHtml(collectionIntervalMinutes(target) ?? "?")} 分钟</em>
          </span>
          <span class="collection-target-count">${escapeHtml(count)}</span>
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
      <span>为避免超大网页拖垮采集服务，部分正文会合并或截断显示；已经保存的完整相邻版本不受影响。</span>
      <span class="collection-diff-hashes">已保留上一版与当前版正文，可查看变化</span>
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
  const discoveredItems = detail.discovered_items || target.discovered_items || [];
  const runTotal = Number(detail.run_count ?? target.run_count ?? runs.length);
  const versionTotal = Number(detail.version_count ?? target.version_count ?? versions.length);
  const discoveredTotal = Number(detail.discovered_item_count ?? target.discovered_item_count ?? discoveredItems.length);
  const status = collectionTargetStatus(target);
  const paused = target.enabled === false || status === "paused";
  const targetType = target.target_type || "web_page";
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
      <div><dt>来源类型</dt><dd>${escapeHtml(LABELS.collectionTargetType[targetType] || targetType)}</dd></div>
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
                ${run.discovery ? `<small>发现 ${escapeHtml(run.discovery.new_item_count ?? 0)} 新条目 · 重复 ${escapeHtml(run.discovery.duplicate_item_count ?? 0)} · 无效 ${escapeHtml(run.discovery.invalid_item_count ?? 0)}</small>` : ""}
                ${collectionRunError(run) ? `<p>${escapeHtml(collectionRunError(run))}</p>` : ""}
              </div>
              ${run.status === "failed" ? `<button class="text-btn warning" type="button" data-collection-action="retry" data-run-id="${escapeHtml(run.id)}" ${paused ? 'disabled title="请先恢复周期"' : ""}>重试</button>` : ""}
            </article>`).join("") : '<div class="collection-empty"><p>尚无运行记录。</p></div>'}
          ${runs.length < runTotal ? `<button class="text-btn collection-load-more" type="button" data-collection-action="more-runs" data-target-id="${escapeHtml(target.id)}">加载更早运行</button>` : ""}
        </div>
      </section>
      <section>
        ${
          targetType === "rss_feed"
            ? `
        <div class="collection-section-heading"><div><span class="panel-kicker">DISCOVERED ITEMS</span><h3>发现条目</h3></div><span>已载入 ${discoveredItems.length} / 共 ${discoveredTotal} 个</span></div>
        <div class="collection-version-list">
          ${discoveredItems.length ? discoveredItems.map((item) => `
            <article class="collection-version">
              <div>
                <strong>${escapeHtml(item.title || item.source_url || "未命名条目")}</strong>
                <span>${escapeHtml(LABELS.discoveredItemStatus[item.status] || item.status || "未知状态")}</span>
                <small>${formatDate(item.last_seen_at || item.updated_at || item.created_at, true)}</small>
                ${item.error ? `<small>${escapeHtml(item.error)}</small>` : ""}
              </div>
              ${item.intake_item_id ? `<button class="text-btn" type="button" data-collection-action="review" data-intake-id="${escapeHtml(item.intake_item_id)}">打开条目材料</button>` : ""}
            </article>`).join("") : '<div class="collection-empty"><p>成功检查 RSS 后才会出现条目。</p></div>'}
        </div>`
            : `
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
        <div id="collection-diff" class="collection-diff">${renderCollectionDiff(state.collectionDiff)}</div>`
        }
      </section>
    </div>`;
}

async function loadCollectionTarget(targetId, { preserveDiff = false } = {}) {
  const requestSerial = ++state.collectionRequestSerial;
  if (targetId && !collectionTargetIsInScope(targetId)) {
    state.selectedCollectionTargetId = null;
    state.selectedCollectionTarget = null;
    state.collectionDiff = null;
    renderCollectionTargets();
    $("#collection-detail").innerHTML = '<div class="collection-empty"><strong>来源不在当前专题</strong><p>已停止读取，避免显示或操作其他专题来源。</p></div>';
    return;
  }
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
  const refreshSerial = ++state.collectionRefreshSerial;
  const scopeInvestigationId = state.collectionScopeInvestigationId;
  state.collectionBusy = true;
  let collectionRetryDelayMs = null;
  const previousTargetId = state.selectedCollectionTargetId;
  try {
    const [summary, targets, scopeDetail] = await Promise.all([
      api("/pldr-api/v1/collection/summary"),
      api("/pldr-api/v1/collection/targets"),
      scopeInvestigationId
        ? api(API_ROUTES.investigation(scopeInvestigationId))
        : Promise.resolve(null),
    ]);
    if (
      refreshSerial !== state.collectionRefreshSerial
      || scopeInvestigationId !== state.collectionScopeInvestigationId
      || !$("#collection-modal").open
    ) return;
    state.collectionSummary = summary;
    state.collectionTargets = targets.items || targets.targets || [];
    if (scopeInvestigationId) {
      state.collectionScopeTargetIds = new Set((scopeDetail?.collection_targets || []).map((target) => target.id));
      state.investigationDetails.set(scopeInvestigationId, scopeDetail);
    } else {
      state.collectionScopeTargetIds = null;
    }
    renderCollectionSummary();
    const visibleTargets = collectionVisibleTargets();
    const requestedTargetId = preferredTargetId || previousTargetId || null;
    const requestedTargetIsVisible = visibleTargets.some((target) => target.id === requestedTargetId);
    const targetLeftScope = Boolean(requestedTargetId && !requestedTargetIsVisible && scopeInvestigationId);
    const targetId = requestedTargetIsVisible
      ? requestedTargetId
      : targetLeftScope
        ? null
        : visibleTargets[0]?.id || null;
    state.selectedCollectionTargetId = targetId;
    if (!targetId) {
      state.collectionRequestSerial += 1;
      state.collectionDiffRequestSerial += 1;
      state.selectedCollectionTarget = null;
      state.collectionDiff = null;
    }
    renderCollectionTargets();
    if (targetLeftScope) {
      $("#collection-detail").innerHTML = '<div class="collection-empty"><strong>来源已不在本专题</strong><p>为避免误操作其他专题来源，当前选择已清除。请刷新专题或选择仍在本专题的来源。</p></div>';
      toast("原来源已不属于当前专题，已停止显示和操作。", "error", 7000);
    } else {
      await loadCollectionTarget(targetId, { preserveDiff: targetId === previousTargetId });
    }
    renderMetrics();
  } catch (error) {
    if (
      refreshSerial !== state.collectionRefreshSerial
      || scopeInvestigationId !== state.collectionScopeInvestigationId
      || !$("#collection-modal").open
    ) return;
    collectionRetryDelayMs = 15000;
    if (scopeInvestigationId) state.collectionScopeTargetIds = new Set();
    state.selectedCollectionTargetId = null;
    state.selectedCollectionTarget = null;
    state.collectionDiff = null;
    $("#collection-summary").innerHTML = `<div class="collection-error"><strong>来源监测不可用</strong><p>${escapeHtml(error.message)}</p></div>`;
    $("#collection-target-list").innerHTML = '<div class="collection-empty"><p>没有伪造运行记录；请检查后端服务。</p></div>';
    $("#collection-detail").innerHTML = `<div class="collection-error"><strong>无法读取运行与版本</strong><p>${scopeInvestigationId ? "无法确认专题范围，因此没有显示任何全局来源。" : "请恢复后端连接后重试。"}</p></div>`;
  } finally {
    if (refreshSerial === state.collectionRefreshSerial) {
      state.collectionBusy = false;
      scheduleCollectionPoll(collectionRetryDelayMs);
    }
  }
}

async function openCollectionModal(preferredTargetId = null, scopeInvestigationId = null) {
  const modal = $("#collection-modal");
  const destination = state.investigations.find((item) => item.id === state.pendingCollectionInvestigationId) || null;
  const scope = state.investigations.find((item) => item.id === scopeInvestigationId) || null;
  state.collectionRefreshSerial += 1;
  state.collectionRequestSerial += 1;
  state.collectionDiffRequestSerial += 1;
  state.selectedCollectionTargetId = null;
  state.selectedCollectionTarget = null;
  state.collectionDiff = null;
  state.collectionScopeInvestigationId = scopeInvestigationId;
  state.collectionScopeInvestigationTitle = scope?.title || null;
  state.collectionScopeTargetIds = scopeInvestigationId ? new Set() : null;
  const modalTitle = preferredTargetId && scope
    ? `“${scope.title}”的可靠来源`
    : destination
    ? `为“${destination.title}”添加可靠来源`
    : scope
      ? `“${scope.title}”的可靠来源`
      : "可靠来源监测";
  $("#collection-modal-title").textContent = modalTitle;
  $("#collection-modal-title").title = modalTitle;
  if (typeof modal.showModal === "function") modal.showModal();
  else modal.setAttribute("open", "");
  await refreshCollectionData(preferredTargetId);
}

function closeCollectionModal() {
  const modal = $("#collection-modal");
  if (typeof modal.close === "function") modal.close();
  else modal.removeAttribute("open");
  if (state.collectionPollTimer) window.clearTimeout(state.collectionPollTimer);
  state.collectionPollTimer = null;
  state.collectionRequestSerial += 1;
  state.collectionDiffRequestSerial += 1;
  state.collectionReviewSerial += 1;
  state.collectionRefreshSerial += 1;
  state.collectionSubmitSerial += 1;
  state.collectionBusy = false;
  state.pendingCollectionInvestigationId = null;
  state.collectionScopeInvestigationId = null;
  state.collectionScopeInvestigationTitle = null;
  state.collectionScopeTargetIds = null;
  state.selectedCollectionTargetId = null;
  state.selectedCollectionTarget = null;
  state.collectionDiff = null;
  $("#collection-modal-title").textContent = "可靠来源监测";
  $("#collection-modal-title").removeAttribute("title");
  const button = $("#collection-add");
  button.disabled = false;
  button.textContent = "添加来源";
}

async function verifyCollectionActionScope(targetId, { intakeId = null } = {}) {
  const scopeInvestigationId = state.collectionScopeInvestigationId;
  if (!scopeInvestigationId) return { allowed: true, stale: false };
  const detail = await api(API_ROUTES.investigation(scopeInvestigationId));
  if (
    scopeInvestigationId !== state.collectionScopeInvestigationId
    || !$("#collection-modal").open
  ) return { allowed: false, stale: true };
  const linkedTargetIds = new Set((detail.collection_targets || []).map((target) => target.id));
  const linkedIntakeIds = new Set((detail.intake_items || []).map((item) => item.id));
  state.collectionScopeTargetIds = linkedTargetIds;
  state.investigationDetails.set(scopeInvestigationId, detail);
  renderCollectionSummary();
  renderCollectionTargets();
  const allowed = linkedTargetIds.has(targetId) && (!intakeId || linkedIntakeIds.has(intakeId));
  if (!allowed) {
    state.collectionRequestSerial += 1;
    state.selectedCollectionTargetId = null;
    state.selectedCollectionTarget = null;
    state.collectionDiff = null;
    renderCollectionTargets();
    $("#collection-detail").innerHTML = `<div class="collection-empty"><strong>${intakeId ? "来源或材料已不在本专题" : "来源已不在本专题"}</strong><p>已停止本次操作；这里不会改为操作全局或其他专题来源。</p></div>`;
    toast(intakeId ? "这份来源或材料已不属于当前专题，已停止打开审核。" : "这个来源已不属于当前专题，已停止操作。", "error", 7500);
  }
  return { allowed, stale: false };
}

async function submitCollectionTarget(event) {
  event.preventDefault();
  if (state.collectionBusy) return;
  const requestSerial = ++state.collectionSubmitSerial;
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
        target_type: $("#collection-target-type").value,
        url: $("#collection-url").value.trim(),
        interval_seconds: Number($("#collection-interval").value) * 60,
        language: "auto",
        run_immediately: $("#collection-run-immediately").checked,
        ...(isServerInvestigation(destination) ? { investigation_id: destination.id, actor: "analyst" } : {}),
      }),
    });
    const run = result.run || result.queued_run || null;
    const runFailed = run?.status === "failed";
    toast(runFailed ? `来源已保存，但首次检查失败：${collectionRunError(run) || "未知错误"}` : run?.status === "queued" ? "可靠来源已保存，首次检查已进入持久队列。" : "可靠来源已保存。新材料只会进入待审箱。", runFailed ? "error" : "success", 7000);
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
    if (requestSerial !== state.collectionSubmitSerial || !$("#collection-modal").open) {
      refreshData({ keepSelection: true, quiet: true }).catch(() => null);
      refreshInvestigationDirectory().catch(() => null);
      toast("可靠来源已保存；你已切换页面，所以没有改变当前视图。", "info", 7500);
      return;
    }
    $("#collection-source-form").reset();
    $("#collection-run-immediately").checked = true;
    state.collectionBusy = false;
    await refreshCollectionData(result.target?.id);
    try {
      await refreshData({ keepSelection: true, quiet: true });
      await refreshInvestigationDirectory();
      if (requestSerial !== state.collectionSubmitSerial || !$("#collection-modal").open) {
        toast("可靠来源已保存；你已切换页面，所以没有改变当前视图。", "info", 7500);
        return;
      }
      if (destination && state.activeInvestigationId === destination.id) await loadInvestigationWorkspace(destination.id, { quiet: true });
    } catch (error) {
      toast(`来源已保存，但专题指标刷新失败：${error.message}`, "warning", 7000);
    }
  } catch (error) {
    if (requestSerial !== state.collectionSubmitSerial) {
      toast(`后台添加来源失败：${error.message || "未知错误"}`, "error", 7500);
      return;
    }
    toast(`添加来源失败：${error.message}`, "error", 7000);
  } finally {
    if (requestSerial === state.collectionSubmitSerial) {
      state.collectionBusy = false;
      button.disabled = false;
      button.textContent = "添加来源";
    }
  }
}

async function handleCollectionAction(action, node) {
  if (state.collectionBusy) return;
  if (action === "review") {
    const intakeId = node.dataset.intakeId;
    const scopeInvestigationId = state.collectionScopeInvestigationId;
    const targetId = state.selectedCollectionTargetId;
    const requestSerial = ++state.collectionReviewSerial;
    if (scopeInvestigationId) {
      state.collectionBusy = true;
      try {
        const verification = await verifyCollectionActionScope(targetId, { intakeId });
        if (
          requestSerial !== state.collectionReviewSerial
          || !$("#collection-modal").open
          || targetId !== state.selectedCollectionTargetId
          || scopeInvestigationId !== state.collectionScopeInvestigationId
        ) return;
        if (!verification.allowed || verification.stale) return;
      } catch (error) {
        if (requestSerial === state.collectionReviewSerial) {
          toast(`无法确认专题范围：${error.message || "专题可能已不存在"}`, "error", 7500);
        }
        return;
      } finally {
        if (requestSerial === state.collectionReviewSerial) state.collectionBusy = false;
      }
    }
    if (requestSerial !== state.collectionReviewSerial || !$("#collection-modal").open) return;
    closeCollectionModal();
    await openIntakeModal(intakeId, false, scopeInvestigationId);
    return;
  }
  if (action === "diff") {
    const targetId = state.selectedCollectionTargetId;
    const runId = node.dataset.runId;
    const requestSerial = ++state.collectionDiffRequestSerial;
    state.collectionBusy = true;
    try {
      const verification = await verifyCollectionActionScope(targetId);
      if (verification.stale || !verification.allowed || requestSerial !== state.collectionDiffRequestSerial) return;
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
    } finally {
      if (requestSerial === state.collectionDiffRequestSerial) {
        state.collectionBusy = false;
        scheduleCollectionPoll();
      }
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
      const verification = await verifyCollectionActionScope(targetId);
      if (verification.stale || !verification.allowed) return;
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
      if (node.isConnected) node.disabled = false;
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
  let actionAttempted = false;
  try {
    const verification = await verifyCollectionActionScope(targetId);
    if (verification.stale || !verification.allowed) return;
    actionAttempted = true;
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
    if (node.isConnected) node.disabled = false;
    if (actionAttempted) await refreshCollectionData(targetId);
    try {
      if (actionAttempted) await refreshData({ keepSelection: true, quiet: true });
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
      api(`/pldr-api/v1/intake?limit=${GLOBAL_INTAKE_LOAD_LIMIT}&include_detail=false&visibility=active`).catch(() => ({ items: [] })),
      api("/pldr-api/v1/collection/summary").catch(() => null),
    ]);
    state.overview = overview;
    state.events = overview.events || [];
    state.sources = sources.items || [];
    state.config = config;
    state.collectionSummary = collectionSummary;
    renderSearchProvider();
    state.globalIntakeItems = (intakeList.items || []).filter((item) => ACTIVE_INTAKE_STATUSES.has(item.status));
    if (!state.intakeScopeInvestigationId && state.intakeVisibility === "active") {
      state.intakeItems = [...state.globalIntakeItems];
      if (!state.selectedIntakeId) {
        state.selectedIntakeId = state.intakeItems.find((item) => item.status === "candidate_ready")?.id || null;
      }
      renderIntakeList();
    }
    renderTopic();
    renderMetrics();
    renderSources();
    applyFilters();

    const selectableEvents = state.filteredEvents;
    const preferredSelection = preferredEventId
      && selectableEvents.some((event) => event.id === preferredEventId)
      ? preferredEventId
      : null;
    const target = previousSelection && selectableEvents.some((event) => event.id === previousSelection)
      ? previousSelection
      : preferredSelection || selectableEvents[0]?.id;
    const shouldSyncUrl = syncSelectionUrl
      ?? new URL(window.location.href).searchParams.has("event");
    if (target) await selectEvent(target, { open: false, syncUrl: shouldSyncUrl });
    else {
      state.selectedEventRequestSerial += 1;
      state.selectedId = null;
      state.selectedEvent = null;
      state.selectedEventError = null;
      $("#btn-report").disabled = true;
      renderAssessment();
      renderGaps();
      if ($("#event-drawer")?.classList.contains("open")) renderDrawer();
    }

    $("#system-state-text").textContent = config?.model_configured
      ? "证据链已连接 · 模型已配置"
      : "证据链已连接 · 基础草稿模式";
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
  $("#event-overview-return").addEventListener("click", () => {
    const investigationId = state.eventOverviewScopeInvestigationId;
    if (investigationId) openInvestigation(investigationId, "outcomes", { section: "events" });
  });
  $("#btn-create-investigation").addEventListener("click", openInvestigationCreateModal);
  $("#btn-home-search").addEventListener("click", () => openExternalSearchModal());
  $("#btn-home-import").addEventListener("click", () => openImportModal());
  $("#btn-investigation-back").addEventListener("click", () => showInvestigationHome());
  $("#investigation-create-close").addEventListener("click", closeInvestigationCreateModal);
  $("#investigation-create-cancel").addEventListener("click", closeInvestigationCreateModal);
  $$('input[name="investigation-create-mode"]').forEach((input) => input.addEventListener("change", syncInvestigationModeFields));
  $("#investigation-create-name").addEventListener("input", syncInvestigationStarterSuggestions);
  $("#investigation-create-question").addEventListener("input", () => {
    $("#investigation-create-question").dataset.autoSuggested = "false";
    $("#investigation-create-question-note").textContent = "核心问题将用于组织后续资料和成果。";
  });
  $("#investigation-create-keyword").addEventListener("input", () => {
    $("#investigation-create-keyword").dataset.autoSeeded = "false";
  });
  $("#investigation-create-form").addEventListener("submit", submitInvestigationCreate);
  $("#search-destination").addEventListener("change", async () => {
    updateDestinationFields("search");
    const destinationId = currentSearchDestinationId();
    switchSearchWorkspaceContext(destinationId);
    renderSearchHistory();
    renderSearchResults();
    await loadSearchHistory(destinationId);
  });
  $("#import-destination").addEventListener("change", () => updateDestinationFields("import"));
  $("#search").addEventListener("input", applyFilters);
  $("#importance-filter").addEventListener("change", applyFilters);
  $("#language-filter").addEventListener("change", applyFilters);
  $("#contested-filter").addEventListener("change", applyFilters);
  $("#btn-refresh").addEventListener("click", async () => {
    const scopedInvestigationId = state.eventOverviewScopeInvestigationId;
    await refreshData();
    await refreshInvestigationDirectory();
    if (scopedInvestigationId && state.eventOverviewScopeInvestigationId === scopedInvestigationId) {
      const scopedInvestigation = eventOverviewInvestigation();
      if (scopedInvestigation) {
        try {
          await ensureEventOverviewScopeLoaded(scopedInvestigation, { force: true });
          if (state.eventOverviewScopeInvestigationId !== scopedInvestigationId) return;
          renderTopic();
          renderSources();
          applyFilters();
          await reconcileEventOverviewSelection();
          renderGaps();
          renderMetrics();
        } catch (error) {
          toast(`本专题事件范围刷新失败：${error.message || "未知错误"}`, "error", 7000);
        }
      }
    } else if (activeInvestigation()) {
      await loadInvestigationWorkspace(state.activeInvestigationId, { quiet: true });
    }
  });
  $("#btn-report").addEventListener("click", () => generateReport());
  $("#btn-collection").addEventListener("click", () => {
    state.pendingCollectionInvestigationId = null;
    openCollectionModal(null, null);
  });
  $("#btn-search").addEventListener("click", openExternalSearchModal);
  $("#btn-import").addEventListener("click", openImportModal);
  $("#btn-intake").addEventListener("click", () => openIntakeModal(null, false, null));
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
  $("#search-load-more").addEventListener("click", loadMoreExternalSearch);
  $("#search-select-page").addEventListener("click", selectVisibleSearchPage);
  $("#search-select-loaded").addEventListener("click", () => selectSearchResults(state.searchResults.map((result) => result.id)));
  $("#search-clear-selection").addEventListener("click", clearSearchSelection);
  $("#search-result-filter").addEventListener("input", renderSearchResults);
  $("#search-result-filter").addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
  $("#search-result-state").addEventListener("change", renderSearchResults);
  $("#search-history-toggle").addEventListener("click", () => {
    const panel = $("#search-history-panel");
    const collapsed = panel.classList.toggle("collapsed");
    $("#search-history-toggle").textContent = collapsed ? "展开" : "收起";
    $("#search-history-toggle").setAttribute("aria-expanded", String(!collapsed));
  });
  $("#collection-close").addEventListener("click", closeCollectionModal);
  $("#collection-refresh").addEventListener("click", () => refreshCollectionData());
  $("#collection-source-form").addEventListener("submit", submitCollectionTarget);
  $("#intake-close").addEventListener("click", closeIntakeModal);
  const invalidateReviewForm = (event) => {
    if (event.target.closest(".review-form") && !event.target.closest("#intake-reject-panel")) {
      invalidateIntakePreview("字段已变化；请重新预览正式区变化后再确认。");
    }
  };
  $("#intake-modal").addEventListener("input", invalidateReviewForm);
  $("#intake-modal").addEventListener("change", invalidateReviewForm);

  document.addEventListener("click", async (event) => {
    const errorExpander = event.target.closest("[data-expand-error]");
    if (errorExpander) {
      const details = errorExpander.closest(".operational-error")?.querySelector("details");
      if (details) {
        details.open = true;
        details.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      return;
    }
    if (event.target.closest("[data-retry-selected-event]")) {
      await selectEvent(state.selectedId, { open: true, tab: state.drawerTab, syncUrl: false });
      return;
    }
    const traceCopy = event.target.closest("[data-copy-trace]");
    if (traceCopy) {
      navigator.clipboard?.writeText(traceCopy.dataset.copyTrace).then(
        () => toast("诊断编号已复制。", "success", 2600),
        () => toast("浏览器未授予剪贴板权限。", "error", 4200),
      );
      return;
    }
    const searchVisibility = event.target.closest("[data-search-history-visibility]");
    if (searchVisibility) {
      await setSearchHistoryVisibility(searchVisibility.dataset.searchHistoryVisibility);
      return;
    }
    const searchRunAction = event.target.closest("[data-search-run-action]");
    if (searchRunAction) {
      await updateSearchRunVisibility(searchRunAction.dataset.searchRunId, searchRunAction.dataset.searchRunAction);
      return;
    }
    const historyRun = event.target.closest("[data-search-history-run]");
    if (historyRun) {
      openSearchHistoryRun(historyRun.dataset.searchHistoryRun);
      return;
    }
    if (event.target.closest("[data-search-history-retry]")) {
      await loadSearchHistory(state.searchHistoryInvestigationId || currentSearchDestinationId());
      return;
    }
    const intakeStep = event.target.closest("button[data-intake-step]");
    if (intakeStep && intakeStep.closest("#intake-modal")) {
      setIntakeMobileStep(intakeStep.dataset.intakeStep);
      return;
    }
    const investigationTab = event.target.closest("[data-investigation-tab]");
    if (investigationTab) {
      setInvestigationTab(investigationTab.dataset.investigationTab);
      $("#investigation-more-menu")?.removeAttribute("open");
      return;
    }
    const investigationSection = event.target.closest("[data-investigation-section]");
    if (investigationSection) {
      setInvestigationSection(investigationSection.dataset.investigationSection);
      return;
    }
    const findSource = event.target.closest("[data-investigation-find-source]");
    if (findSource) {
      await openExternalSearchModal(state.activeInvestigationId, {
        keyword: String(findSource.dataset.investigationFindSource || "").slice(0, 180),
        autoStart: true,
      });
      return;
    }
    const investigationAction = event.target.closest("[data-investigation-action]");
    if (investigationAction) {
      handleInvestigationAction(investigationAction.dataset.investigationAction, investigationAction);
      return;
    }
    const investigationEvent = event.target.closest("[data-investigation-event]");
    if (investigationEvent) {
      await openInvestigationEvent(investigationEvent.dataset.investigationEvent, state.activeInvestigationId);
      return;
    }
    const assignment = event.target.closest("[data-investigation-assignment]");
    if (assignment) {
      const investigationId = assignment.dataset.investigationId;
      if (investigationId) {
        await openInvestigation(investigationId, "overview");
        await openIntakeModal(assignment.dataset.intakeId || null, false, investigationId);
      }
      else if (assignment.dataset.intakeId) openIntakeModal(assignment.dataset.intakeId);
      return;
    }
    const investigationCard = event.target.closest("[data-investigation-id]");
    if (investigationCard && investigationCard.classList.contains("investigation-card")) {
      openInvestigation(investigationCard.dataset.investigationId, "outcomes");
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
    if (event.target.closest("[data-search-retry-query]")) {
      retryCurrentSearchQuery();
      return;
    }
    const intakeVisibility = event.target.closest("[data-intake-visibility]");
    if (intakeVisibility) {
      await setIntakeVisibility(intakeVisibility.dataset.intakeVisibility);
      return;
    }
    const intakeSelectAll = event.target.closest("[data-intake-select-all]");
    if (intakeSelectAll) {
      state.intakeItems.filter((item) => item.status === "candidate_ready" && !recordIsArchived(item)).forEach((item) => {
        if (intakeSelectAll.checked) state.selectedIntakeIds.add(item.id);
        else state.selectedIntakeIds.delete(item.id);
      });
      renderIntakeList();
      return;
    }
    const intakeSelect = event.target.closest("[data-intake-select]");
    if (intakeSelect) {
      if (intakeSelect.checked) state.selectedIntakeIds.add(intakeSelect.dataset.intakeSelect);
      else state.selectedIntakeIds.delete(intakeSelect.dataset.intakeSelect);
      renderIntakeList();
      return;
    }
    const intakeBatch = event.target.closest("[data-intake-batch]");
    if (intakeBatch) {
      await handleIntakeBatch(intakeBatch.dataset.intakeBatch);
      return;
    }
    const intakeNode = event.target.closest("[data-intake-id]");
    if (intakeNode) {
      const requestContext = {
        serial: ++state.intakeRequestSerial,
        scopeInvestigationId: state.intakeScopeInvestigationId,
      };
      invalidateIntakePreview();
      state.selectedIntakeId = intakeNode.dataset.intakeId;
      renderIntakeList();
      const current = selectedIntakeItem();
      if (!current?.material || !Array.isArray(current?.candidates)) {
        $("#intake-detail").innerHTML = '<div class="panel-empty">正在读取原始材料、固定快照与候选…</div>';
        try {
          await loadIntakeDetail(state.selectedIntakeId, requestContext);
          if (requestContext.serial !== state.intakeRequestSerial) return;
        } catch (error) {
          if (requestContext.serial !== state.intakeRequestSerial) return;
          $("#intake-detail").innerHTML = renderOperationalError(error, { stage: "fetch" });
          return;
        }
      }
      renderIntakeDetail(selectedIntakeItem());
      setIntakeMobileStep(1);
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

  window.addEventListener("popstate", async () => {
    const routeHref = window.location.href;
    const params = new URLSearchParams(window.location.search);
    const investigationId = params.get("investigation");
    const tab = params.get("tab") || "overview";
    const view = params.get("view");
    const requestedEvent = params.get("event");
    if (["classic", "events"].includes(view) || (!investigationId && (requestedEvent || params.get("event_scope")))) {
      const applied = await showClassicWorkspace({ syncUrl: false, scopeInvestigationId: params.get("event_scope") });
      if (!applied || window.location.href !== routeHref) return;
      if (requestedEvent && eventOverviewEvents().some((event) => event.id === requestedEvent)) {
        await selectEvent(requestedEvent, { open: true, syncUrl: false });
      } else if ($("#event-drawer")?.classList.contains("open")) {
        closeDrawer();
      }
    } else if (investigationId) openInvestigation(investigationId, tab, { syncUrl: false, section: params.get("section") });
    else showInvestigationHome({ syncUrl: false });
  });
}

async function init() {
  bindEvents();
  try {
    const routeParams = new URLSearchParams(window.location.search);
    const initialRouteHref = window.location.href;
    const requestedEvent = routeParams.get("event");
    await refreshData({
      keepSelection: false,
      quiet: true,
      preferredEventId: requestedEvent,
      syncSelectionUrl: false,
    });
    await refreshInvestigationDirectory();
    if (window.location.href !== initialRouteHref) return;
    const requestedInvestigation = routeParams.get("investigation");
    const eventOverviewRoute = ["classic", "events"].includes(routeParams.get("view"))
      || Boolean(!requestedInvestigation && (requestedEvent || routeParams.get("event_scope")));
    if (eventOverviewRoute) {
      const applied = await showClassicWorkspace({ syncUrl: false, scopeInvestigationId: routeParams.get("event_scope") });
      if (!applied || window.location.href !== initialRouteHref) return;
      if (requestedEvent && eventOverviewEvents().some((event) => event.id === requestedEvent)) {
        await selectEvent(requestedEvent, { open: true, syncUrl: false });
      } else if (requestedEvent) {
        const url = new URL(window.location.href);
        url.searchParams.delete("event");
        history.replaceState(null, "", url);
        if ($("#event-drawer")?.classList.contains("open")) closeDrawer();
      }
    } else if (requestedInvestigation) {
      await openInvestigation(requestedInvestigation, routeParams.get("tab") || "overview", { syncUrl: false, section: routeParams.get("section") });
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
