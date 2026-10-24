import { useEffect, useMemo, useRef, useState } from "react";
import { api, AuthError, REVIEW_LABEL, RESPONSE_STEPS, speak } from "./api";
import { useFeed } from "./useFeed";

const LAYOUTS = [
  [2, "2 × 2 · 4 feeds"],
  [3, "3 × 3 · 9 feeds"],
  [4, "4 × 4 · 16 feeds"],
  [6, "6 × 6 · 36 feeds"],
  [9, "9 × 9 · 81 feeds"],
];

export default function Dashboard({ user = "Operator", navigate }) {
  const [layout, setLayout] = useState(3);
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState(null); // {kind: "incident"|"camera", id}
  const [voice, setVoice] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAgents, setShowAgents] = useState(false);
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  const { cameras, incidents, agents, online } = useFeed({
    onEmergency: (inc) => {
      setFocus({ kind: "incident", id: inc.id });
      if (voiceRef.current) speak(inc.alert_text);
    },
  });

  const guard = (e) => setError(e.message);
  const camList = useMemo(() => Object.values(cameras).sort((a, b) => a.id.localeCompare(b.id)), [cameras]);
  const all = Object.values(incidents);
  const emergencies = all.filter((i) => i.emergency === true && i.response_status !== "resolved").sort((a, b) => b.detected_at.localeCompare(a.detected_at));
  const review = all
    .filter((i) => i.emergency === null && i.review_status !== "dismissed")
    .sort((a, b) => b.priority_score - a.priority_score || b.video_ts - a.video_ts);

  const cells = layout * layout;
  const pages = Math.max(1, Math.ceil(camList.length / cells));
  const shown = camList.slice(page * cells, (page + 1) * cells);
  const focusInc = focus?.kind === "incident" ? incidents[focus.id] : null;
  const focusCam = cameras[focusInc?.camera_id || focus?.id] || camList[0];

  const onFiles = async (files) => {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      [...files].forEach((f) => form.append("files", f));
      await api("/api/upload", { method: "POST", form });
    } catch (e) {
      guard(e);
    }
    setBusy(false);
  };

  return (
    <div className="desk">
      <header className="bar">
        <div className="brand-group">
          <div className="brand-icon">ID</div>
          <h1>Incident Desk</h1>
        </div>
        <div className="bar-stats">
          <button className="pill pill-agents" onClick={() => setShowAgents(true)} title="View Active AI & Vision Agents">
            <span className="pulse-dot" />
            <span>5 Agents Active</span>
          </button>
          <span className={emergencies.length ? "pill pill-critical" : "pill"}>{emergencies.length} active emergencies</span>
          <span className={review.length ? "pill pill-high" : "pill"}>{review.length} awaiting review</span>
          <span className={online ? "pill pill-ok" : "pill pill-high"}>{online ? "Connected" : "Reconnecting"}</span>
        </div>
        <div className="bar-actions">
          <select aria-label="Camera layout" value={layout} onChange={(e) => { setLayout(+e.target.value); setPage(0); }}>
            {LAYOUTS.map(([n, label]) => <option key={n} value={n}>{label}</option>)}
          </select>
          <label className="btn"><input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} /> Voice on emergency</label>
          <label className="btn btn-primary">
            {busy ? "Uploading…" : "Add videos"}
            <input type="file" accept="video/*" multiple hidden onChange={(e) => onFiles(e.target.files)} />
          </label>
          {camList.length > 0 && <button className="btn" onClick={() => api("/api/reset", { method: "POST" }).then(() => setFocus(null)).catch(guard)}>Start over</button>}
          <span className="pill" style={{ borderColor: "var(--line-orange)", color: "var(--orange-deep)", fontWeight: 600 }}>{user}</span>
        </div>
      </header>

      {showAgents && <AgentsModal agents={agents} onClose={() => setShowAgents(false)} />}

      {error && <div className="error">{error}</div>}

      {camList.length === 0 ? (
        <Dropzone onFiles={onFiles} />
      ) : (
        <main className="floor">
          <section className="center">
            <FocusPanel camera={focusCam} incident={focusInc} navigate={navigate} guard={guard} />
            {layout >= 6 && <p className="muted note">Large layouts show status tiles only. Video plays in the focus panel above, and analysis runs on the server either way.</p>}
            <div className="tiles" style={{ gridTemplateColumns: `repeat(${layout}, minmax(0, 1fr))` }}>
              {shown.map((c) => (
                <Tile key={c.id} cam={c} compact={layout >= 6} active={focusCam?.id === c.id} onClick={() => setFocus({ kind: "camera", id: c.id })} />
              ))}
            </div>
            {pages > 1 && (
              <div className="pager">
                <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                <span className="muted">Page {page + 1} of {pages}</span>
                <button className="btn" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </section>
          <aside className="side">
            <div className="panel">
              <h2>Confirmed emergencies</h2>
              {emergencies.length === 0 && <p className="muted">None. An incident appears here once a reviewer confirms it as an emergency.</p>}
              <ul className="queue">
                {emergencies.map((i) => (
                  <li key={i.id}>
                    <button className={`q sev-critical ${focus?.id === i.id ? "q-on" : ""}`} onClick={() => setFocus({ kind: "incident", id: i.id })}>
                      <strong>{i.label}</strong>
                      <span>{i.camera_name} · {i.video_ts_text}</span>
                      <em>{RESPONSE_STEPS.find(([k]) => k === i.response_status)?.[1]}</em>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div className="panel">
              <h2>Review queue</h2>
              {review.length === 0 && <p className="muted">No detections waiting. New ones appear here and are emailed to the reviewer.</p>}
              <ul className="queue">
                {review.map((i) => (
                  <li key={i.id}>
                    <button className={`q sev-${i.severity}`} onClick={() => navigate(`/incidents/${i.id}`)}>
                      <strong>{i.label}</strong>
                      <span>{i.camera_name} · {i.video_ts_text} · AI severity {i.severity}</span>
                      <em>{REVIEW_LABEL[i.review_status]}</em>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="muted note">A detection is not an emergency until a reviewer confirms it in the workbench.</p>
            </div>
          </aside>
        </main>
      )}
    </div>
  );
}

function Dropzone({ onFiles }) {
  const [over, setOver] = useState(false);
  return (
    <div
      className={over ? "drop drop-over" : "drop"}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}
    >
      <h2>Drop CCTV videos here</h2>
      <p>Each video becomes a camera with its own analysis task on the server. Detections go to the review queue and are emailed to the reviewer.</p>
      <label className="btn btn-primary">
        Choose videos
        <input type="file" accept="video/*" multiple hidden onChange={(e) => onFiles(e.target.files)} />
      </label>
    </div>
  );
}

function Tile({ cam, active, compact, onClick }) {
  const state = { analyzing: "Analysing", complete: "Complete", error: "Error", queued: "Queued" }[cam.status];
  return (
    <button className={`tile sev-${cam.severity} ${compact ? "tile-compact" : ""} ${active ? "tile-active" : ""}`} onClick={onClick}>
      {!compact && <video src={cam.video_url} muted autoPlay playsInline preload="metadata" />}
      <div className="tile-top">
        <strong>{cam.id}</strong>
        {!compact && <span>{cam.name}</span>}
        {cam.incident_count > 0 && <em className={`badge sev-${cam.severity}`}>{cam.incident_count}</em>}
      </div>
      {!compact && (
        <div className="tile-bottom">
          <span>{state}</span>
          <div className="progress"><div style={{ width: `${Math.round(cam.progress * 100)}%` }} /></div>
        </div>
      )}
    </button>
  );
}

function FocusPanel({ camera, incident, navigate, guard }) {
  const ref = useRef(null);
  const ts = incident?.video_ts;

  useEffect(() => {
    const v = ref.current;
    if (!v || ts == null) return;
    const seek = () => { v.currentTime = Math.max(0, ts - 2); v.play().catch(() => {}); };
    v.readyState >= 1 ? seek() : v.addEventListener("loadedmetadata", seek, { once: true });
  }, [incident?.id, camera?.id]);

  if (!camera) return null;
  const setStatus = (response_status) => api(`/api/incidents/${incident.id}`, { method: "PATCH", body: { response_status } }).catch(guard);
  return (
    <div className={`focus ${incident ? "sev-critical" : ""}`}>
      {incident && (
        <div className="banner sev-critical">
          <strong>Emergency confirmed · {incident.camera_name} · {incident.video_ts_text}</strong>
          <span>{incident.label}. Reviewer decision recorded in {incident.id}.</span>
        </div>
      )}
      <div className="stage"><video ref={ref} key={camera.id} src={camera.video_url} controls muted autoPlay playsInline /></div>
      <div className="focus-bar">
        <span>{camera.id} · {camera.name}</span>
        {incident && (
          <div className="seg" role="group" aria-label="Response status">
            {RESPONSE_STEPS.map(([k, label]) => (
              <button key={k} className={incident.response_status === k ? "on" : ""} onClick={() => setStatus(k)}>{label}</button>
            ))}
            <button onClick={() => navigate(`/incidents/${incident.id}`)}>Open workbench</button>
          </div>
        )}
      </div>
    </div>
  );
}

function AgentsModal({ agents, onClose }) {
  const fallbackAgents = [
    {
      id: "video_analysis",
      name: "VideoAnalysisAgent",
      role: "Vision & Ingestion Pipeline",
      method: "YOLOv8 + Optical Difference Monitoring",
      status: "active",
      description: "Extracts video frames, samples at target FPS, maintains rolling pre-buffer, and tracks bounding boxes across frames.",
    },
    {
      id: "incident_detection",
      name: "IncidentDetectionAgent",
      role: "Heuristic & Anomaly Classifier",
      method: "Multi-class Geometry & Spatio-temporal Rules",
      status: "active",
      description: "Classifies collisions, falls, restricted zone intrusions, traffic congestions, and abnormal motion surges.",
    },
    {
      id: "priority_alert",
      name: "PriorityAlertAgent",
      role: "Dynamic Triage & Alerting",
      method: "Context-correlated Priority Matrix",
      status: "active",
      description: "Calculates real-time priority score, elevates severity on compound events, and prepares spoken voice alerts.",
    },
    {
      id: "investigation",
      name: "InvestigationAgent",
      role: "Correlation & SOP Investigator",
      method: "Cross-incident Query & Protocol Retrieval",
      status: "active",
      description: "Retrieves matching historical incidents, constructs camera timeline, and provides standard operating procedures.",
    },
    {
      id: "reporting",
      name: "ReportingAgent",
      role: "Forensic & Evidence Synthesizer",
      method: "SHA-256 Cryptographic Chain + Optional Vision LLM",
      status: "active",
      description: "Assembles pre/event/post evidence frames, computes tamper-evident cryptographic hashes, and drafts incident reports.",
    },
  ];

  const list = agents && agents.length > 0 ? agents : fallbackAgents;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2>Autonomous Vision & Triage Agents</h2>
            <p className="muted" style={{ margin: "4px 0 0", fontSize: "13px" }}>
              5 specialized logical agents coordinate in real-time to analyze footage, evaluate risk, correlate precedents, and preserve chain of custody.
            </p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close agents modal">✕</button>
        </div>
        <div className="agent-grid">
          {list.map((ag) => (
            <div key={ag.id} className="agent-card">
              <div className="agent-card-header">
                <span className="agent-name">{ag.name}</span>
                <span className="agent-tag">● {ag.status || "active"}</span>
              </div>
              <span className="agent-role">{ag.role}</span>
              <span className="agent-method">{ag.method}</span>
              <p className="agent-desc">{ag.description}</p>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "8px" }}>
          <button className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
