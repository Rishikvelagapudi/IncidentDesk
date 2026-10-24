import { useEffect, useRef, useState } from "react";
import { api, AuthError, REVIEW_LABEL } from "./api";
import { useFeed } from "./useFeed";

const ACTIONS = [
  ["confirm_incident", "Confirm incident", "btn"],
  ["confirm_emergency", "Confirm emergency", "btn btn-danger"],
  ["not_emergency", "Not an emergency", "btn"],
  ["request_review", "Request further review", "btn"],
];
const PHASE = { pre: "Before", event: "Detection", post: "After" };

export default function Workbench({ id, user = "Operator", navigate }) {
  const { cameras, incidents } = useFeed();
  const [fetched, setFetched] = useState(null);
  const [error, setError] = useState("");
  const [selId, setSelId] = useState(null);
  const [zoom, setZoom] = useState(null);
  const [compare, setCompare] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [integrity, setIntegrity] = useState(null);
  const [summary, setSummary] = useState(null);
  const [checkedSOP, setCheckedSOP] = useState({});
  const video = useRef(null);

  const guard = (e) => setError(e.message);
  useEffect(() => { api(`/api/incidents/${id}`).then(setFetched).catch(guard); }, [id]);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && setZoom(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const inc = incidents[id] || fetched;
  if (error && !inc) return <Shell navigate={navigate} user={user}><p className="form-error">{error}</p></Shell>;
  if (!inc) return <Shell navigate={navigate} user={user}><p className="muted">Loading incident…</p></Shell>;

  const frames = [...inc.evidence].sort((a, b) => a.video_ts - b.video_ts);
  const sel = frames.find((f) => f.frame_id === selId) || frames.find((f) => f.phase === "event") || frames[0];
  const src = cameras[inc.camera_id]?.video_url || `/api/videos/${inc.camera_id}`;
  const seek = (t, play = false) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, t);
    play ? v.play().catch(() => {}) : v.pause();
  };
  const pick = (f) => { setSelId(f.frame_id); seek(f.video_ts); };
  const act = async (action) => {
    setBusy(true);
    setError("");
    try { await api(`/api/incidents/${id}/review`, { method: "POST", body: { action, reason } }); setReason(""); }
    catch (e) { guard(e); }
    setBusy(false);
  };
  const decision = inc.emergency === true ? "Emergency confirmed" : inc.emergency === false ? "Not an emergency" : "No emergency decision yet";

  return (
    <Shell navigate={navigate} user={user}>
      <section className="panel summary">
        <h2>{inc.id} · {inc.label}</h2>
        <dl className="facts wide">
          <dt>Incident type</dt><dd>{inc.category} · {inc.short}</dd>
          <dt>Camera</dt><dd>{inc.camera_id}</dd>
          <dt>Location</dt><dd>{inc.location}</dd>
          <dt>Detection time</dt><dd>Video {inc.video_ts_text} · detected {new Date(inc.detected_at).toLocaleString()}</dd>
          <dt>Preliminary severity</dt><dd>{inc.severity} <span className="muted">({inc.severity_note})</span></dd>
          <dt>Review status</dt><dd>{REVIEW_LABEL[inc.review_status]} · {decision}</dd>
        </dl>
        <h3>Incident summary</h3>
        <textarea rows={3} value={summary ?? inc.summary} onChange={(e) => setSummary(e.target.value)} />
        {summary !== null && summary !== inc.summary && (
          <button className="btn" onClick={() => api(`/api/incidents/${id}`, { method: "PATCH", body: { summary } }).then(() => setSummary(null)).catch(guard)}>Save summary</button>
        )}
        <p className="muted note">Text source: {inc.description_source}. The summary describes the detector output and saved frames. Edit it if the footage shows something different.</p>
      </section>

      <section className="panel">
        <h2>Evidence timeline</h2>
        {!inc.evidence_complete && <p className="muted note">Frames after the event are still being collected.</p>}
        <ul className="strip">
          {frames.map((f) => (
            <li key={f.frame_id}>
              <button className={`thumb ${sel?.frame_id === f.frame_id ? "thumb-on" : ""} phase-${f.phase}`} onClick={() => pick(f)}>
                <img src={f.url} alt={`${PHASE[f.phase]} frame at ${f.video_ts_text}`} loading="lazy" />
                <span>{f.video_ts_text} · {PHASE[f.phase]}</span>
              </button>
            </li>
          ))}
        </ul>
        {sel && (
          <div className="selected">
            <button className="enlarge" onClick={() => setZoom(sel)} aria-label="Enlarge frame"><img src={sel.url} alt="" /></button>
            <div>
              <h3>{sel.frame_id} · {sel.video_ts_text}</h3>
              <p>{sel.finding}</p>
              <p className="muted">Model confidence: {sel.confidence == null ? "not available" : `${Math.round(sel.confidence * 100)}%`} · SHA-256 {sel.sha256.slice(0, 12)}…</p>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Original footage</h2>
        <div className={compare ? "compare" : ""}>
          <video ref={video} src={src} controls muted playsInline onLoadedMetadata={() => seek(inc.video_ts)} />
          {compare && sel && <img src={sel.url} alt={`Evidence frame ${sel.video_ts_text}`} />}
        </div>
        <div className="actions">
          <button className="btn" onClick={() => seek(inc.video_ts, true)}>Jump to incident ({inc.video_ts_text})</button>
          <button className="btn" onClick={() => setCompare(!compare)}>{compare ? "Hide frame comparison" : "Compare with evidence frame"}</button>
        </div>
      </section>

      <section className="panel">
        <h2>AI findings</h2>
        {inc.findings.map((f, i) => (
          <dl className="facts wide" key={i}>
            <dt>Detected event</dt><dd>{f.event}</dd>
            <dt>Objects</dt><dd>{f.objects}</dd>
            <dt>Timestamp</dt><dd>{f.timestamp}</dd>
            <dt>Confidence</dt><dd>{Math.round(f.confidence * 100)}% rule score, not a calibrated probability</dd>
            <dt>Evidence</dt><dd>{f.evidence}</dd>
            <dt>Method</dt><dd>{f.method}</dd>
          </dl>
        ))}
        <h3>Uncertainties and limitations</h3>
        <ul className="plain">{inc.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>

      {inc.procedure && inc.procedure.length > 0 && (
        <section className="panel">
          <h2>Investigation Agent · Standard Operating Procedure (SOP)</h2>
          <p className="muted note" style={{ margin: "0 0 10px" }}>
            Recommended protocol for {inc.label}. Check off steps during your assessment:
          </p>
          <ul className="checklist">
            {inc.procedure.map((step, idx) => (
              <li key={idx} className="checklist-item">
                <input
                  type="checkbox"
                  id={`sop-${idx}`}
                  checked={!!checkedSOP[idx]}
                  onChange={(e) => setCheckedSOP({ ...checkedSOP, [idx]: e.target.checked })}
                />
                <label
                  htmlFor={`sop-${idx}`}
                  style={{
                    cursor: "pointer",
                    textDecoration: checkedSOP[idx] ? "line-through" : "none",
                    opacity: checkedSOP[idx] ? 0.7 : 1,
                  }}
                >
                  <strong>Step {idx + 1}:</strong> {step}
                </label>
              </li>
            ))}
          </ul>

          {inc.similar_incidents && inc.similar_incidents.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h3>Historical Similar Incidents</h3>
              <ul className="plain">
                {inc.similar_incidents.map((s) => (
                  <li key={s.id}>
                    <button
                      className="btn"
                      style={{ padding: "3px 8px", fontSize: "12px", marginRight: "6px" }}
                      onClick={() => navigate(`/incidents/${s.id}`)}
                    >
                      {s.id}
                    </button>
                    {s.camera_name} · video {s.video_ts_text} · {s.emergency ? "Confirmed Emergency" : "Not an emergency"}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {inc.timeline && inc.timeline.length > 1 && (
            <div style={{ marginTop: "16px" }}>
              <h3>Camera Timeline ({inc.camera_name})</h3>
              <ul className="plain">
                {inc.timeline.map((item) => (
                  <li key={item.id}>
                    <strong>{item.video_ts_text}</strong> · {item.label}{" "}
                    {item.id === inc.id ? (
                      <em style={{ color: "var(--orange-deep)" }}>(this incident)</em>
                    ) : (
                      <button
                        className="btn"
                        style={{ padding: "2px 6px", fontSize: "11px", marginLeft: "6px" }}
                        onClick={() => navigate(`/incidents/${item.id}`)}
                      >
                        {item.id}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section className="panel">
        <h2>Reviewer actions</h2>
        <p className="muted note">Detecting an incident does not send emergency resources. Confirm an emergency only after checking the evidence.</p>
        <label>Reason (recorded with your decision)<textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <div className="actions">
          {ACTIONS.map(([key, label, cls]) => <button key={key} className={cls} disabled={busy} onClick={() => act(key)}>{label}</button>)}
        </div>
        {error && <p className="form-error">{error}</p>}
        <h3>Review history</h3>
        {inc.reviews.length === 0 ? <p className="muted">No decisions yet.</p> : (
          <ul className="plain">{inc.reviews.map((r, i) => <li key={i}>{new Date(r.at).toLocaleTimeString()} · {r.user} · {r.action.replace(/_/g, " ")}{r.reason ? ` · ${r.reason}` : ""}</li>)}</ul>
        )}
      </section>

      <section className="panel">
        <h2>Record</h2>
        <dl className="facts wide">
          <dt>Email alert</dt><dd>{inc.email.status}{inc.email.to ? ` to ${inc.email.to}` : ""}{inc.email.detail ? ` · ${inc.email.detail}` : ""}</dd>
          <dt>Original video</dt><dd>{inc.video_ref.file} · SHA-256 {inc.video_ref.sha256?.slice(0, 16)}…</dd>
        </dl>
        <div className="actions">
          <button className="btn" onClick={() => api(`/api/incidents/${id}/integrity`).then(setIntegrity).catch(guard)}>Verify evidence integrity</button>
          {integrity && <span>{integrity.video_ok && integrity.frames.every((f) => f.ok) ? "Video and all frames match their recorded hashes." : "A hash does not match. Treat this evidence as altered."}</span>}
        </div>
        <h3>Audit trail</h3>
        <ul className="plain">{inc.audit.map((a, i) => <li key={i}>{new Date(a.at).toLocaleTimeString()} · {a.actor} · {a.action}</li>)}</ul>
        <h3>Access log</h3>
        <ul className="plain">{inc.access_log.slice(-8).map((a, i) => <li key={i}>{new Date(a.at).toLocaleString()} · {a.user} opened this report</li>)}</ul>
      </section>

      {zoom && (
        <div className="lightbox" role="dialog" aria-label="Enlarged evidence frame" onClick={() => setZoom(null)}>
          <img src={zoom.url} alt={`Frame ${zoom.frame_id}`} />
          <p>{zoom.video_ts_text} · {zoom.finding}</p>
        </div>
      )}
    </Shell>
  );
}

function Shell({ navigate, user, children }) {
  return (
    <div className="desk">
      <header className="bar">
        <div className="brand-group" onClick={() => navigate("/")}>
          <div className="brand-icon">ID</div>
          <h1>Incident Workbench</h1>
        </div>
        <div className="bar-stats" />
        <div className="bar-actions">
          <span className="pill" style={{ borderColor: "var(--line-orange)", color: "var(--orange-deep)", fontWeight: 600 }}>{user}</span>
          <button className="btn" onClick={() => navigate("/")}>Back to dashboard</button>
        </div>
      </header>
      <main className="bench">{children}</main>
    </div>
  );
}
