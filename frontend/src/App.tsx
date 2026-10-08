import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Code2,
  Database,
  Download,
  FileText,
  FlaskConical,
  Layers3,
  Loader2,
  LockKeyhole,
  Radio,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";

type Obj = Record<string, any>;
type Incident = {
  id: string;
  title: string;
  service: string;
  status: string;
  created_at: string;
  updated_at: string;
  connector: string;
  evidence: Obj;
  diagnosis: Obj;
  plan: Obj;
  plan_hash?: string;
  recovery: Obj;
  postmortem: string;
  events?: Obj[];
};
type Session = {
  actor: string;
  tenant: string;
  role: string;
  demo_mode: boolean;
  provider: string;
};
const titles: Obj = {
  investigating: "Investigating",
  awaiting_approval: "Needs approval",
  executing: "Rolling back",
  verifying: "Verifying recovery",
  resolved: "Resolved",
  escalated: "Escalated",
  observing: "Observing",
  rejected: "Rejected",
};
const tools: Obj = {
  get_deployment_diff: "Deployment diff",
  query_metrics: "Service metrics",
  query_logs: "Application logs",
  query_traces: "Distributed traces",
};
const time = (date: string) =>
  new Date(date).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
const short = (id: string) => id.slice(0, 7);

function Badge({ status }: { status: string }) {
  return (
    <span className={`badge ${status}`}>
      <span />
      {titles[status] || status}
    </span>
  );
}
function Json({ value }: { value: any }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export default function App() {
  const [token, setToken] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState("");
  const [incident, setIncident] = useState<Incident | null>(null);
  const [page, setPage] = useState("incidents");
  const [tab, setTab] = useState("investigation");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [scenario, setScenario] = useState("pool_exhaustion");
  const [live, setLive] = useState(false);
  const [evalReport, setEvalReport] = useState<Obj | null>(null);
  const [audit, setAudit] = useState<Obj | null>(null);
  const [filter, setFilter] = useState("all");
  const [evidenceTab, setEvidenceTab] = useState("metrics");
  const [connected, setConnected] = useState(false);
  const api = useCallback(
    async (path: string, body?: Obj) => {
      const response = await fetch(path, {
        method: body ? "POST" : "GET",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) {
        const data = await response
          .json()
          .catch(() => ({ detail: "Request failed" }));
        throw new Error(data.detail || "Request failed");
      }
      return response.json();
    },
    [token],
  );
  const refresh = useCallback(async () => {
    const [me, items] = await Promise.all([
      api("/api/session"),
      api("/api/incidents"),
    ]);
    setSession(me);
    setIncidents(items);
    setConnected(true);
  }, [api]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      refresh().catch((e) => {
        if (alive) {
          setConnected(false);
          setError(e.message);
        }
      });
    load();
    const interval = setInterval(load, 2500);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [refresh]);
  useEffect(() => {
    if (!selected && incidents.length) setSelected(incidents[0].id);
  }, [incidents, selected]);
  useEffect(() => {
    let alive = true;
    if (!selected) return;
    const load = () =>
      api(`/api/incidents/${selected}`)
        .then((i) => {
          if (alive) setIncident(i);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    load();
    const interval = setInterval(load, 1500);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [api, selected]);
  useEffect(() => {
    if (token || !session?.demo_mode) return;
    const source = new EventSource("/api/events");
    source.addEventListener("incident", () => {
      refresh().catch(() => {});
      if (selected)
        api(`/api/incidents/${selected}`)
          .then(setIncident)
          .catch(() => {});
    });
    return () => source.close();
  }, [api, refresh, selected, token, session?.demo_mode]);
  async function run() {
    setBusy(true);
    setError("");
    try {
      const i = await api("/api/incidents", { scenario, live });
      setSelected(i.id);
      setIncident(i);
      setPage("incidents");
      setTab("investigation");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function decide(decision: string) {
    if (!incident) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/incidents/${incident.id}/decision`, {
        decision,
        plan_hash: incident.plan_hash,
      });
      setIncident(await api(`/api/incidents/${incident.id}`));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function runEval() {
    setBusy(true);
    setError("");
    try {
      setEvalReport(await api("/api/evaluations", {}));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function verifyAudit() {
    setError("");
    try {
      setAudit(await api("/api/audit/verify"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function download() {
    if (!incident?.postmortem) return;
    const url = URL.createObjectURL(
      new Blob([incident.postmortem], { type: "text/markdown" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `sentinel-${short(incident.id)}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  const active = incidents.filter((i) =>
    ["investigating", "awaiting_approval", "executing", "verifying"].includes(
      i.status,
    ),
  );
  const awaiting = incidents.filter((i) => i.status === "awaiting_approval");
  const resolved = incidents.filter((i) => i.status === "resolved");
  const visible = incidents.filter(
    (i) =>
      filter === "all" ||
      (filter === "active"
        ? active.some((a) => a.id === i.id)
        : i.status === filter),
  );
  const metrics = incident?.evidence.metrics;
  const calls = incident?.events?.filter((e) => e.kind === "tool.read") || [];
  const canOperate = session?.role === "operator";
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="brand-mark">
            <Layers3 size={22} />
          </span>
          sentinel<span className="brand-dot">.</span>
        </a>
        <div className="workspace">
          <span className="workspace-avatar">
            {(session?.tenant || "S")[0].toUpperCase()}
          </span>
          <div>
            <strong>
              {session?.tenant === "demo"
                ? "Demo workspace"
                : session?.tenant || "Workspace"}
            </strong>
            <small>
              {session?.demo_mode ? "ISOLATED FAULT LAB" : "INCIDENT COMMAND"}
            </small>
          </div>
          <ChevronDown size={14} />
        </div>
        <p className="nav-label">WORKSPACE</p>
        <button
          className={`nav ${page === "incidents" ? "active" : ""}`}
          onClick={() => setPage("incidents")}
        >
          <Radio size={17} />
          Incidents<span className="nav-count">{active.length}</span>
        </button>
        <button
          className={`nav ${page === "evaluations" ? "active" : ""}`}
          onClick={() => setPage("evaluations")}
        >
          <FlaskConical size={17} />
          Replay evaluations
        </button>
        <button
          className={`nav ${page === "audit" ? "active" : ""}`}
          onClick={() => {
            setPage("audit");
            verifyAudit();
          }}
        >
          <ShieldCheck size={17} />
          Audit trail
        </button>
        <div className="sidebar-spacer" />
        <div className="policy-note">
          <LockKeyhole size={16} />
          <strong>Human control, always.</strong>
          <p>
            Investigation reads evidence. Remediation waits for a scoped
            approval.
          </p>
          <span>POLICY · CONSERVATIVE V1</span>
        </div>
        <div className="sidebar-bottom">
          <span className={`online-dot ${connected ? "" : "offline"}`} />
          {connected
            ? "Control plane connected"
            : "Connecting to control plane"}
          <small>v0.1.0</small>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            Workspace
            <ChevronRight size={14} />
            <strong>
              {page === "incidents"
                ? "Incident command"
                : page === "evaluations"
                  ? "Replay evaluations"
                  : "Audit trail"}
            </strong>
          </div>
          <span className="environment">
            <span />
            {session?.demo_mode ? "LOCAL DEMO" : "AUTHENTICATED"}
          </span>
        </header>
        <nav className="mobile-nav" aria-label="Mobile workspace navigation">
          <button
            onClick={() => setPage("incidents")}
            className={page === "incidents" ? "active" : ""}
          >
            <Radio size={14} />
            Incidents
          </button>
          <button
            onClick={() => setPage("evaluations")}
            className={page === "evaluations" ? "active" : ""}
          >
            <FlaskConical size={14} />
            Evaluations
          </button>
          <button
            onClick={() => {
              setPage("audit");
              verifyAudit();
            }}
            className={page === "audit" ? "active" : ""}
          >
            <ShieldCheck size={14} />
            Audit
          </button>
        </nav>
        <div className="main-body">
          <div className="page-heading">
            <div>
              <p className="eyebrow">OBSERVE. INVESTIGATE. RECOVER.</p>
              <h1>
                {page === "incidents"
                  ? "Incident command"
                  : page === "evaluations"
                    ? "Test before you trust."
                    : "Every action, accounted for."}
              </h1>
              <p className="subtitle">
                {page === "incidents"
                  ? "From first signal to verified recovery. Evidence at every step."
                  : page === "evaluations"
                    ? "Replay reproducible failures against a versioned investigation policy."
                    : "A tamper-evident record of investigation, approval, and execution."}
              </p>
            </div>
            <div className="heading-icon">
              <Activity size={30} strokeWidth={1.5} />
            </div>
          </div>
          {error && (
            <div className="error" role="alert">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {!session && (
            <section className="card auth-card">
              <h3>Connect to your workspace</h3>
              <p>
                Enter a configured bearer token. Local demo mode connects
                automatically.
              </p>
              <input
                aria-label="Bearer token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Workspace token"
                autoComplete="off"
              />
            </section>
          )}
          {page === "incidents" && (
            <>
              <div className="stats">
                <Stat
                  label="ACTIVE INCIDENTS"
                  value={active.length}
                  detail="Under investigation"
                  icon={<Radio size={18} />}
                />
                <Stat
                  label="AWAITING APPROVAL"
                  value={awaiting.length}
                  detail="Human decision required"
                  icon={<LockKeyhole size={18} />}
                />
                <Stat
                  label="VERIFIED RECOVERIES"
                  value={resolved.length}
                  detail="Two recovery windows passed"
                  icon={<ShieldCheck size={18} />}
                />
                <Stat
                  label="INVESTIGATION ENGINE"
                  value={session?.provider || "…"}
                  detail={
                    session?.provider === "baseline"
                      ? "Deterministic evidence rules"
                      : "Configured language model"
                  }
                  icon={<Code2 size={18} />}
                />
              </div>
              <section className="demo-bar">
                <div className="demo-icon">
                  <Terminal size={20} />
                </div>
                <div className="demo-copy">
                  <strong>Break it. Investigate it. Recover it.</strong>
                  <span>
                    {live
                      ? "Inject a real failure into the isolated checkout service."
                      : "Replay a synthetic incident. No API keys required."}
                  </span>
                </div>
                <div className="demo-controls">
                  <select
                    aria-label="Incident scenario"
                    value={scenario}
                    onChange={(e) => setScenario(e.target.value)}
                  >
                    <option value="pool_exhaustion">
                      Connection pool exhaustion
                    </option>
                    <option value="memory_leak">Memory regression</option>
                    <option value="dependency_timeout">
                      Dependency timeout
                    </option>
                    <option value="healthy">Healthy service</option>
                    {!live && (
                      <option value="ambiguous">Incomplete telemetry</option>
                    )}
                  </select>
                  {session?.demo_mode && (
                    <label className="live-toggle">
                      <input
                        type="checkbox"
                        checked={live}
                        onChange={(e) => {
                          setLive(e.target.checked);
                          if (scenario === "ambiguous")
                            setScenario("pool_exhaustion");
                        }}
                      />
                      Live sandbox
                    </label>
                  )}
                  <button
                    className="button dark"
                    disabled={busy || !canOperate}
                    onClick={run}
                  >
                    {busy ? (
                      <Loader2 className="spin" size={15} />
                    ) : (
                      <ArrowRight size={15} />
                    )}
                    Run scenario
                  </button>
                </div>
              </section>
              <div className="incident-layout">
                <section className="card incident-list">
                  <div className="card-heading">
                    <h2>
                      Incidents <span>{incidents.length}</span>
                    </h2>
                    <select
                      aria-label="Filter incidents"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    >
                      <option value="all">All states</option>
                      <option value="active">Active</option>
                      <option value="resolved">Resolved</option>
                      <option value="escalated">Escalated</option>
                    </select>
                  </div>
                  <div className="list-label">
                    <span>INCIDENT / SERVICE</span>
                    <span>STATE</span>
                  </div>
                  {visible.map((i) => (
                    <button
                      key={i.id}
                      className={`incident-row ${selected === i.id ? "selected" : ""}`}
                      onClick={() => {
                        setSelected(i.id);
                        setTab("investigation");
                      }}
                    >
                      <div className="row-top">
                        <span
                          className={`incident-dot ${i.status === "resolved" ? "green" : ""}`}
                        />
                        <span className="mono">
                          INC-{short(i.id).toUpperCase()}
                        </span>
                        <span className="row-time">{time(i.created_at)}</span>
                      </div>
                      <strong>{i.title}</strong>
                      <div className="row-bottom">
                        <span>
                          <Database size={12} /> {i.service}{" "}
                          <small>
                            {i.connector === "sandbox" ? "LIVE" : "REPLAY"}
                          </small>
                        </span>
                        <Badge status={i.status} />
                      </div>
                    </button>
                  ))}
                  {!visible.length && (
                    <div className="list-empty">
                      <CircleDot size={26} />
                      <strong>No incidents yet</strong>
                      <p>
                        Run a scenario above to follow an investigation from
                        alert to recovery.
                      </p>
                    </div>
                  )}
                  <div className="list-footer">
                    <span className="online-dot" />
                    Persistent incident state
                  </div>
                </section>
                <section className="card incident-detail">
                  {incident ? (
                    <>
                      <div className="detail-header">
                        <div>
                          <span className="mono small">
                            INC-{short(incident.id).toUpperCase()}{" "}
                            <span className="muted">/ {incident.service}</span>
                          </span>
                          <h2>{incident.title}</h2>
                        </div>
                        <Badge status={incident.status} />
                      </div>
                      <div className="tabs">
                        {["investigation", "evidence", "timeline"].map((t) => (
                          <button
                            className={tab === t ? "active" : ""}
                            onClick={() => setTab(t)}
                            key={t}
                          >
                            {t[0].toUpperCase() + t.slice(1)}
                            {t === "evidence" && (
                              <span>
                                {
                                  Object.values(incident.evidence).filter(
                                    (v) => v && typeof v === "object" && v.id,
                                  ).length
                                }
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                      {tab === "investigation" && (
                        <div className="detail-content">
                          <div className="section-label">
                            <Activity size={14} />
                            INVESTIGATION TRACE
                            <span>{calls.length} read tools</span>
                          </div>
                          <div className="tool-grid">
                            {Object.keys(tools).map((name) => {
                              const call = calls.find(
                                (c) => c.payload.tool === name,
                              );
                              return (
                                <div
                                  className={`tool-item ${call ? "complete" : ""}`}
                                  key={name}
                                >
                                  <span className="tool-check">
                                    {call ? <Check size={13} /> : <span />}
                                  </span>
                                  <div>
                                    <strong>{tools[name]}</strong>
                                    <span>
                                      {call
                                        ? `${call.payload.latency_ms} ms · ${call.payload.evidence_id}`
                                        : "Waiting for evidence"}
                                    </span>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          {incident.diagnosis.cause ? (
                            <>
                              <div className="diagnosis">
                                <div className="diagnosis-label">
                                  <span className="online-dot" />
                                  PROBABLE CAUSE
                                  <span>
                                    {Math.round(
                                      incident.diagnosis.confidence * 100,
                                    )}
                                    % confidence
                                  </span>
                                </div>
                                <h3>
                                  {incident.diagnosis.cause.replaceAll(
                                    "_",
                                    " ",
                                  )}
                                </h3>
                                <p>{incident.diagnosis.summary}</p>
                                <div className="citations">
                                  {incident.diagnosis.evidence_ids?.map(
                                    (id: string) => (
                                      <button
                                        key={id}
                                        onClick={() => {
                                          setTab("evidence");
                                          setEvidenceTab(
                                            id.split("-")[0] === "deployment"
                                              ? "deployment"
                                              : id.split("-")[0],
                                          );
                                        }}
                                      >
                                        <FileText size={11} />
                                        {id}
                                      </button>
                                    ),
                                  )}
                                </div>
                              </div>
                              {metrics && (
                                <div className="observations">
                                  <Observation
                                    label="Error rate"
                                    value={`${(metrics.error_rate * 100).toFixed(1)}%`}
                                    danger={metrics.error_rate > 0.02}
                                  />
                                  <Observation
                                    label="P95 latency"
                                    value={`${metrics.p95_ms.toFixed(0)} ms`}
                                  />
                                  <Observation
                                    label="Request samples"
                                    value={metrics.window_requests}
                                  />
                                </div>
                              )}
                              {incident.plan.requires_approval && (
                                <div
                                  className={`approval-box ${incident.status === "resolved" ? "approved" : ""}`}
                                >
                                  <div className="approval-title">
                                    <LockKeyhole size={17} />
                                    <h3>
                                      {incident.status === "awaiting_approval"
                                        ? "Remediation needs your approval"
                                        : incident.status === "resolved"
                                          ? "Rollback verified"
                                          : "Scoped remediation plan"}
                                    </h3>
                                  </div>
                                  <p>
                                    Restore{" "}
                                    <code>{incident.plan.target_revision}</code>{" "}
                                    from{" "}
                                    <code>
                                      {incident.plan.expected_revision}
                                    </code>{" "}
                                    on <strong>{incident.plan.scope}</strong>.
                                  </p>
                                  <div className="plan-reason">
                                    {incident.plan.reason}
                                  </div>
                                  {incident.status === "awaiting_approval" && (
                                    <div className="approval-actions">
                                      <button
                                        className="button green"
                                        disabled={busy || !canOperate}
                                        onClick={() => decide("approve")}
                                      >
                                        <ShieldCheck size={15} />
                                        Approve rollback
                                      </button>
                                      <button
                                        className="button subtle"
                                        disabled={busy || !canOperate}
                                        onClick={() => decide("reject")}
                                      >
                                        Reject plan
                                      </button>
                                      <span>
                                        Bound to this plan and revision
                                      </span>
                                    </div>
                                  )}
                                </div>
                              )}
                              {incident.status === "resolved" && (
                                <div className="recovery">
                                  <div>
                                    <Check size={18} />
                                    <strong>Recovery confirmed</strong>
                                    <span>2 sampling windows passed</span>
                                  </div>
                                  <div className="recovery-values">
                                    <span>
                                      Errors{" "}
                                      <strong>
                                        {(
                                          (incident.recovery.samples?.[1]
                                            ?.error_rate || 0) * 100
                                        ).toFixed(1)}
                                        %
                                      </strong>
                                    </span>
                                    <span>
                                      P95{" "}
                                      <strong>
                                        {incident.recovery.samples?.[1]?.p95_ms?.toFixed(
                                          0,
                                        )}{" "}
                                        ms
                                      </strong>
                                    </span>
                                    <button
                                      className="text-button"
                                      onClick={download}
                                    >
                                      <Download size={13} />
                                      Postmortem
                                    </button>
                                  </div>
                                </div>
                              )}
                              {incident.status === "escalated" && (
                                <div className="escalation">
                                  <strong>
                                    Escalated for human investigation
                                  </strong>
                                  <p>
                                    {incident.recovery.verified === false
                                      ? "Rollback did not satisfy recovery criteria. The incident stays open for triage."
                                      : incident.plan.reason}
                                  </p>
                                </div>
                              )}
                              {incident.status === "observing" && (
                                <div className="recovery">
                                  <div>
                                    <Check size={18} />
                                    <strong>
                                      Healthy service. No remediation proposed.
                                    </strong>
                                  </div>
                                </div>
                              )}
                              {incident.status === "rejected" && (
                                <div className="escalation">
                                  <strong>Plan rejected</strong>
                                  <p>
                                    No rollback will execute. The approval
                                    decision is recorded in the audit trail.
                                  </p>
                                </div>
                              )}
                            </>
                          ) : (
                            <div className="investigating">
                              <Loader2 className="spin" size={23} />
                              <strong>Collecting evidence</strong>
                              <p>
                                Correlating the current deployment, metrics,
                                logs, and traces.
                              </p>
                            </div>
                          )}
                        </div>
                      )}
                      {tab === "evidence" && (
                        <div className="detail-content">
                          <div className="evidence-tabs">
                            {["metrics", "deployment", "logs", "traces"].map(
                              (k) => (
                                <button
                                  key={k}
                                  className={evidenceTab === k ? "active" : ""}
                                  onClick={() => setEvidenceTab(k)}
                                >
                                  {k}
                                </button>
                              ),
                            )}
                          </div>
                          <p className="evidence-note">
                            Captured at investigation time. Source content is
                            treated as untrusted data.
                          </p>
                          <Json
                            value={
                              incident.evidence[evidenceTab] || {
                                status: "Not captured yet",
                              }
                            }
                          />
                          {incident.recovery.samples && (
                            <>
                              <h3>Recovery samples</h3>
                              <Json value={incident.recovery} />
                            </>
                          )}
                        </div>
                      )}
                      {tab === "timeline" && (
                        <div className="detail-content">
                          <div className="timeline">
                            {incident.events?.map((event) => (
                              <div
                                className="timeline-item"
                                key={event.sequence}
                              >
                                <span
                                  className={`timeline-dot ${event.kind === "recovery.verified" ? "green" : ""}`}
                                />
                                <div>
                                  <span className="timeline-time">
                                    {time(event.timestamp)}{" "}
                                    <small>#{event.sequence}</small>
                                  </span>
                                  <strong>
                                    {event.kind.replaceAll(".", " / ")}
                                  </strong>
                                  <span className="timeline-actor">
                                    {event.actor}
                                  </span>
                                  <details>
                                    <summary>Inspect record</summary>
                                    <Json value={event} />
                                  </details>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="detail-footer">
                        <ShieldCheck size={12} />
                        Read tools cannot execute remediation.
                        <span>Updated {time(incident.updated_at)}</span>
                      </div>
                    </>
                  ) : (
                    <div className="detail-empty">
                      <div className="empty-orbit">
                        <Layers3 size={42} strokeWidth={1} />
                      </div>
                      <p className="eyebrow">READY WHEN YOU ARE</p>
                      <h2>Your next incident has a paper trail.</h2>
                      <p>
                        Start with a reproducible failure. Sentinel gathers the
                        evidence, proposes a scoped action, and checks the
                        recovery.
                      </p>
                      <div className="empty-steps">
                        <span>
                          <Radio size={15} />
                          Detect
                        </span>
                        <ArrowRight size={13} />
                        <span>
                          <Code2 size={15} />
                          Investigate
                        </span>
                        <ArrowRight size={13} />
                        <span>
                          <ShieldCheck size={15} />
                          Verify
                        </span>
                      </div>
                    </div>
                  )}
                </section>
              </div>
            </>
          )}
          {page === "evaluations" && (
            <section className="card evaluation-card">
              <div className="card-heading">
                <div>
                  <h2>Synthetic replay suite</h2>
                  <p>
                    100 cases across 5 failure classes. Half contain a hostile
                    log instruction.
                  </p>
                </div>
                <button
                  className="button dark"
                  onClick={runEval}
                  disabled={busy || !canOperate}
                >
                  {busy ? (
                    <Loader2 className="spin" size={15} />
                  ) : (
                    <FlaskConical size={15} />
                  )}
                  Run baseline evaluation
                </button>
              </div>
              <div className="eval-disclaimer">
                This suite validates deterministic rules and policy behavior on
                synthetic fixtures. It does not measure real-world LLM accuracy
                or production MTTR.
              </div>
              {evalReport ? (
                <>
                  <div className="stats eval-stats">
                    <Stat
                      label="ROOT CAUSE ACCURACY"
                      value={`${(evalReport.root_cause_accuracy * 100).toFixed(0)}%`}
                      detail={`${evalReport.cases} synthetic cases`}
                      icon={<Check size={16} />}
                    />
                    <Stat
                      label="UNSAFE PLAN RATE"
                      value={`${(evalReport.unsafe_proposal_rate * 100).toFixed(0)}%`}
                      detail="After conservative policy"
                      icon={<LockKeyhole size={16} />}
                    />
                    <Stat
                      label="P95 INVESTIGATION"
                      value={`${evalReport.p95_investigation_ms.toFixed(2)} ms`}
                      detail="Local baseline, no model calls"
                      icon={<Activity size={16} />}
                    />
                  </div>
                  <table className="eval-table">
                    <thead>
                      <tr>
                        <th>Scenario</th>
                        <th>Cases</th>
                        <th>Correct diagnosis</th>
                        <th>Correct plan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        "pool_exhaustion",
                        "dependency_timeout",
                        "memory_leak",
                        "healthy",
                        "ambiguous",
                      ].map((name) => {
                        const rows = evalReport.results.filter(
                          (r: Obj) => r.scenario === name,
                        );
                        return (
                          <tr key={name}>
                            <td>{name.replaceAll("_", " ")}</td>
                            <td>{rows.length}</td>
                            <td>
                              {rows.filter((r: Obj) => r.correct).length}/
                              {rows.length}
                            </td>
                            <td>
                              {rows.filter((r: Obj) => r.correct_plan).length}/
                              {rows.length}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              ) : (
                <div className="eval-empty">
                  <FlaskConical size={35} strokeWidth={1.4} />
                  <h3>Reproducible evidence. Measurable behavior.</h3>
                  <p>
                    Evaluate root-cause classification, safe plan selection,
                    tool-call counts, and investigation latency.
                  </p>
                </div>
              )}
            </section>
          )}
          {page === "audit" && (
            <section className="card audit-card">
              <div className="card-heading">
                <h2>Audit integrity</h2>
                <button className="button subtle" onClick={verifyAudit}>
                  <ShieldCheck size={15} />
                  Verify hash chain
                </button>
              </div>
              {audit && (
                <div
                  className={`audit-verdict ${audit.valid ? "valid" : "invalid"}`}
                >
                  <ShieldCheck size={28} />
                  <div>
                    <h3>
                      {audit.valid
                        ? "Hash chain is intact"
                        : "Integrity check failed"}
                    </h3>
                    <p>
                      {audit.events} events in this workspace. SHA-256 chain
                      checked against the stored head.
                    </p>
                  </div>
                </div>
              )}
              <p className="evidence-note">
                The chain detects edits and truncation against the stored head.
                A database administrator can rewrite both. Anchor the head
                externally for stronger guarantees.
              </p>
              {audit && <Json value={audit} />}
              <h3>Workspace access</h3>
              <p className="evidence-note">
                Tokens stay in memory. Roles and tenant scope are assigned by
                the server.
              </p>
              <input
                className="token-input"
                type="password"
                aria-label="Switch workspace token"
                placeholder="Enter a configured workspace token"
                value={token}
                onChange={(e) => {
                  setSelected("");
                  setIncident(null);
                  setSession(null);
                  setToken(e.target.value);
                }}
                autoComplete="off"
              />
            </section>
          )}
          <footer className="page-footer">
            <span>
              SENTINEL <small>/ EVIDENCE-DRIVEN OPERATIONS</small>
            </span>
            <span>
              <ArrowDownRight size={13} />
              Built to make decisions reviewable.
            </span>
          </footer>
        </div>
      </main>
    </div>
  );
}
function Stat({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: string | number;
  detail: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="stat">
      <div>
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function Observation({
  label,
  value,
  danger = false,
}: {
  label: string;
  value: string | number;
  danger?: boolean;
}) {
  return (
    <div>
      <span>{label}</span>
      <strong className={danger ? "danger" : ""}>{value}</strong>
    </div>
  );
}
