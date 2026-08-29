const TONE_LABELS = {
  changed: ["有变化", "amber"],
  healthy: ["正常", "green"],
  failed: ["失败", "red"],
  retrying: ["重试中", "amber"],
  current: ["当前", "cyan"],
  candidate_ready: ["候选就绪", "purple"],
  generation_failed: ["生成失败", "red"],
  rejected: ["已驳回", "muted"],
  confirmed: ["已入档", "green"],
  no_change: ["无变化", "muted"],
  success: ["已保存", "green"],
  archived: ["已归档", "muted"],
};

function StatusPill({ status, label, tone }) {
  const config = TONE_LABELS[status] || [label || status, tone || "muted"];
  return (
    <span className={`status-pill tone-${tone || config[1]}`}>
      {label || config[0]}
    </span>
  );
}

function NavButton({ item, active, onClick }) {
  return (
    <button className={`nav-button ${active ? "active" : ""}`} onClick={onClick} type="button">
      <span className="nav-icon" aria-hidden="true">{item.icon}</span>
      <span>{item.label}</span>
      {item.count !== undefined && <span className="nav-count">{item.count}</span>}
    </button>
  );
}

function PageHeading({ eyebrow, title, description, actions }) {
  return (
    <header className="page-heading">
      <div>
        <p className="page-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </header>
  );
}

function Surface({ title, subtitle, actions, children, className = "" }) {
  return (
    <section className={`surface ${className}`}>
      {(title || actions) && (
        <header className="surface-header">
          <div>
            {title && <div className="surface-title">{title}</div>}
            {subtitle && <div className="surface-subtitle">{subtitle}</div>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

function StatCard({ stat, onClick }) {
  const color = {
    cyan: "var(--cyan)",
    amber: "var(--amber)",
    purple: "var(--purple)",
    red: "var(--red)",
  }[stat.tone];
  return (
    <button
      className="stat-card"
      style={{ "--stat-color": color }}
      type="button"
      onClick={onClick}
      aria-label={`${stat.label} ${stat.value}，打开详情`}
    >
      <div className="stat-label">{stat.label}</div>
      <div className="stat-value-row">
        <strong className="stat-value">{stat.value}</strong>
        <span className="stat-delta">{stat.delta}</span>
      </div>
      <div className="stat-hint"><span>所选 24 小时</span><span>查看 →</span></div>
    </button>
  );
}

function AttentionCard({ item, onOpen }) {
  return (
    <button className="attention-card" type="button" onClick={() => onOpen(item)}>
      <span className={`priority-rail ${item.priority}`} aria-label={`优先级 ${item.priority}`}></span>
      <span className="attention-main">
        <h3>{item.title}</h3>
        <span className="attention-meta">
          <span>{item.source}</span><span>{item.time}</span><span>{item.detail}</span>
        </span>
        <p className="attention-reason">{item.reason}</p>
        <span className="badge-row">
          {item.badges.map((badge) => <span className="badge" key={badge}>{badge}</span>)}
        </span>
      </span>
      <span className="attention-go" aria-hidden="true">›</span>
    </button>
  );
}

function HealthRow({ label, detail, value, tone = "green" }) {
  const barColor = tone === "amber" ? "var(--amber)" : tone === "red" ? "var(--red)" : "var(--green)";
  return (
    <div className="health-row">
      <strong>{label}</strong>
      <span>{detail}</span>
      <div className="health-track"><i style={{ width: `${value}%`, background: barColor }} /></div>
    </div>
  );
}

function Filters({ items, value, onChange }) {
  return (
    <div className="filters" role="group" aria-label="状态筛选">
      {items.map((item) => (
        <button
          className={`filter-button ${value === item.value ? "active" : ""}`}
          key={item.value}
          onClick={() => onChange(item.value)}
          type="button"
        >
          {item.label}{item.count !== undefined ? ` ${item.count}` : ""}
        </button>
      ))}
    </div>
  );
}

function SourceTable({ sources, selectedId, onSelect }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>来源</th><th>运行健康</th><th>最近结果</th><th>采集方式</th><th>周期</th>
            <th>上次成功</th><th>最近变化</th><th>下次运行</th><th>版本</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((source) => (
            <tr
              className={selectedId === source.id ? "selected" : ""}
              key={source.id}
              onClick={() => onSelect(source.id)}
              tabIndex="0"
              onKeyDown={(event) => event.key === "Enter" && onSelect(source.id)}
            >
              <td className="source-name"><strong>{source.name}</strong><small>{source.host}</small></td>
              <td><StatusPill status={source.health} /></td>
              <td><StatusPill status={source.lastResult} /></td>
              <td>{source.method}</td><td>{source.cadence}</td><td>{source.lastSuccess}</td>
              <td>{source.lastChange}</td><td>{source.nextRun}</td><td>{source.versions}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!sources.length && <div className="empty-state"><strong>没有符合条件的来源</strong>换一个状态筛选试试。</div>}
    </div>
  );
}

function RunList({ runs }) {
  return (
    <div className="run-list">
      {runs.map((run) => (
        <div className="run-row" key={run.id}>
          <span className="run-time">{run.time}</span>
          <StatusPill status={run.result} />
          <div className="run-detail">{run.detail}<small>{run.id} · {run.duration} · {run.bytes}</small></div>
        </div>
      ))}
    </div>
  );
}

function VersionList({ versions, selected, onSelect }) {
  return (
    <div className="version-list">
      {versions.map((version) => (
        <button
          className={`version-item ${selected === version.id ? "active" : ""}`}
          key={version.id}
          onClick={() => onSelect(version.id)}
          type="button"
        >
          <strong><span>{version.id}</span>{version.status === "current" && <span>当前</span>}</strong>
          <small>{version.time}</small><small>{version.hash}</small>
        </button>
      ))}
    </div>
  );
}

function DiffViewer({ chunks, mode, onlyChanges, oldLabel = "上一版本", newLabel = "当前版本" }) {
  const visible = onlyChanges ? chunks.filter((chunk) => chunk.type !== "same") : chunks;
  if (mode === "split") {
    return (
      <div className="split-diff">
        <div className="split-column">
          <div className="split-heading">{oldLabel}</div>
          {visible.map((chunk, index) => (
            <div className={`split-line ${chunk.type === "removed" ? "removed" : ""} ${!chunk.old ? "empty" : ""}`} key={`old-${index}`}>
              {chunk.old || "—"}
            </div>
          ))}
        </div>
        <div className="split-column">
          <div className="split-heading">{newLabel}</div>
          {visible.map((chunk, index) => (
            <div className={`split-line ${chunk.type === "added" ? "added" : ""} ${!chunk.next ? "empty" : ""}`} key={`new-${index}`}>
              {chunk.next || "—"}
            </div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="diff-document">
      {visible.map((chunk, index) => (
        <React.Fragment key={index}>
          {chunk.type === "removed" && (
            <div className="diff-line removed"><span className="diff-symbol">−</span><span className="diff-copy">{chunk.old}</span></div>
          )}
          {chunk.type === "added" && (
            <div className="diff-line added"><span className="diff-symbol">+</span><span className="diff-copy">{chunk.next}</span></div>
          )}
          {chunk.type === "same" && (
            <div className="diff-line same"><span className="diff-symbol">·</span><span className="diff-copy">{chunk.next}</span></div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function ReviewQueue({ items, selectedId, onSelect }) {
  return (
    <div className="review-queue">
      {items.map((item) => (
        <button
          className={`queue-item ${item.id === selectedId ? "active" : ""} ${!item.hasCandidate ? "disabled" : ""}`}
          key={item.id}
          onClick={() => onSelect(item.id)}
          type="button"
        >
          <span className="queue-topline">
            <span className="queue-type">{item.type}</span><span className="queue-time">{item.time}</span>
          </span>
          <h3>{item.title}</h3><p>{item.source}</p>
          <div className="queue-match">{item.eventMatch}</div>
        </button>
      ))}
    </div>
  );
}

function DocumentPane({ item, candidate, detail, view, setView }) {
  const evidence = candidate?.claims?.map((claim) => claim.evidence) || detail.evidence || [];
  const hasDiff = detail.diff === "sca";
  return (
    <>
      <div className="material-meta">
        <div><small>来源</small><strong>{item.source}</strong></div>
        <div><small>快照</small><strong>{detail.snapshot}</strong></div>
        <div><small>采集时间</small><strong>{detail.capturedAt}</strong></div>
      </div>
      <div className="document-toolbar">
        <div className="segmented">
          <button className={view === "material" ? "active" : ""} onClick={() => setView("material")}>提取正文</button>
          {hasDiff && <button className={view === "diff" ? "active" : ""} onClick={() => setView("diff")}>V3 / V4 Diff</button>}
        </div>
        <StatusPill label="原始材料" tone="cyan" />
      </div>
      {view === "material" ? (
        <article className="document-copy">
          <div className="doc-label">{detail.kicker}</div>
          <h2>{detail.documentTitle}</h2>
          <p>{detail.intro}</p>
          {evidence.map((quote, index) => <p className="evidence-highlight" key={quote}><span className="evidence-marker">E{index + 1}</span>{quote}</p>)}
          <p>{detail.outro}</p>
        </article>
      ) : (
        <DiffViewer chunks={window.PLDR_MOCK_DATA.diff} mode="inline" onlyChanges={false} />
      )}
    </>
  );
}

function CandidatePane({ candidate, disposition, setDisposition, edits, setEdits, onPreview, previewOpen, onConfirm, rejectionReason, setRejectionReason }) {
  const change = (key, value) => setEdits((old) => ({ ...old, [key]: value }));
  const targetText = {
    "新建": `将新建事件“${edits.title}”`,
    "合并": `将合并到 ${candidate.event.match}`,
    "修改": `将修改 ${candidate.event.match} 的摘要与已选主张`,
    "驳回": "不会改变任何正式事件、主张或证据",
  }[disposition];
  const confirmLabel = disposition === "驳回" ? "确认驳回候选" : `确认${disposition}并入档`;
  return (
    <>
      <div className="candidate-banner">
        <span aria-hidden="true">◇</span>
        <span><strong>AI 候选 · 尚未进入正式档案</strong>{candidate.model.name} · {candidate.model.latency} · {candidate.model.tokens} tokens · {candidate.model.cost}</span>
      </div>

      <section className="candidate-section">
        <header className="candidate-section-header"><strong>候选事件</strong><span className="machine-label">MACHINE</span></header>
        <div className="candidate-fields">
          <div className={`field ${edits.title !== candidate.event.title ? "modified" : ""}`}>
            <label><span>事件标题</span>{edits.title !== candidate.event.title && <span className="human-label">人工修改</span>}</label>
            <input value={edits.title} onChange={(e) => change("title", e.target.value)} />
          </div>
          <div className={`field ${edits.summary !== candidate.event.summary ? "modified" : ""}`}>
            <label><span>摘要</span>{edits.summary !== candidate.event.summary && <span className="human-label">人工修改</span>}</label>
            <textarea value={edits.summary} onChange={(e) => change("summary", e.target.value)} />
          </div>
          <div className="field"><label><span>建议归档</span><span className="machine-label">92% 匹配</span></label><input value={candidate.event.match} readOnly /></div>
        </div>
      </section>

      <section className="candidate-section">
        <header className="candidate-section-header"><strong>候选实体</strong><span className="machine-label">{candidate.entities.length} 项</span></header>
        <div className="entity-list">
          {candidate.entities.map((entity) => <div className="entity-row" key={`${entity.name}-${entity.role}`}><span><strong>{entity.name}</strong><small>{entity.type}</small></span><span className="badge">{entity.role}</span></div>)}
        </div>
      </section>

      <section className="candidate-section">
        <header className="candidate-section-header"><strong>候选主张与证据</strong><span className="machine-label">{candidate.claims.length} 项</span></header>
        {candidate.claims.map((claim, index) => (
          <article className="claim-card" key={claim.id}>
            <div className="claim-head"><span className="claim-id">C{index + 1}</span><span>{edits[`claim${index}`] !== claim.text && <span className="human-label">人工修改</span>} <span className="confidence">模型置信 {Math.round(claim.strength * 100)}%</span></span></div>
            <div className="field"><textarea value={edits[`claim${index}`]} onChange={(e) => change(`claim${index}`, e.target.value)} /></div>
            {edits[`claim${index}`] !== claim.text && <div className="machine-original"><span className="machine-label">机器原值</span>{claim.text}</div>}
            <div className="evidence-quote"><span className="evidence-marker">E{index + 1}</span>{claim.evidence}</div>
          </article>
        ))}
      </section>

      {previewOpen && (
        <section className="preview-box">
          <header><strong>确认后的可见变化</strong><span className="formal-label">FORMAL</span></header>
          <div className="preview-copy">
            <strong>{targetText}</strong>；原材料与机器原值都保持不可变。
            {disposition === "驳回" ? (
              <ul><li>驳回原因：{rejectionReason}</li><li>候选标记为已驳回，并保留人工决定。</li><li>正式事件档案和报告区不会出现这些内容。</li></ul>
            ) : (
              <ul>
                <li>写入 {candidate.claims.length} 条人工确认主张，并分别回链原句。</li>
                <li>采用当前人工编辑值；机器原值继续保留用于审计。</li>
                <li>正式报告区可引用这些主张；原材料本身保持不可变。</li>
              </ul>
            )}
          </div>
        </section>
      )}

      <footer className="disposition">
        <div className="disposition-options">
          {["新建", "合并", "修改", "驳回"].map((option) => (
            <button className={`disposition-option ${disposition === option ? "active" : ""}`} key={option} onClick={() => setDisposition(option)}>{option}</button>
          ))}
        </div>
        {disposition === "驳回" && <input className="rejection-input" value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="填写驳回原因（必填）" aria-label="驳回原因" />}
        <div className="disposition-actions">
          <button className="ghost-button button-small" disabled={disposition === "驳回" && !rejectionReason.trim()} onClick={onPreview}>{previewOpen ? "收起预览" : "预览影响"}</button>
          <button className="primary-button button-small" disabled={!previewOpen} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </footer>
    </>
  );
}

function CommandPalette({ open, query, setQuery, items, onChoose, onClose }) {
  if (!open) return null;
  const filtered = items.filter((item) => `${item.label}${item.hint}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="command-overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="command-palette" role="dialog" aria-modal="true" aria-label="全局命令">
        <div className="command-search"><span>⌕</span><input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="跳转页面、查找事件或执行操作…" /></div>
        <div className="command-results">
          {filtered.map((item) => <button className="command-result" key={item.id} onClick={() => onChoose(item)}><span>{item.icon}</span><span>{item.label}</span><small>{item.hint}</small></button>)}
          {!filtered.length && <div className="empty-state">没有匹配的命令</div>}
        </div>
      </div>
    </div>
  );
}

function Toast({ toast }) {
  if (!toast) return null;
  return (
    <div className="toast" role="status">
      <span className="toast-mark">✓</span>
      <span><strong>{toast.title}</strong><span>{toast.detail}</span></span>
    </div>
  );
}

window.PLDR_COMPONENTS = {
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
};
