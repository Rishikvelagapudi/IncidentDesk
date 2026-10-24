# Incident Desk

Multi-camera incident management. Each camera gets its own server-side analysis task. A detection becomes a report with saved evidence, an email goes to the reviewer, and the incident reaches the main dashboard only after a human confirms an emergency.

## Run
```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install && npm run build
cd ../backend && python -m uvicorn main:app --port 8000     # open http://localhost:8000
```
Dev: `npm run dev` in `frontend/` (http://localhost:5173, proxies to :8000). Docker: `docker compose up --build`.
Default logins (change them): `admin / admin123`, `reviewer / review123`. Set `USERS="name:password,..."`.

## Flow
1. **Dashboard**: layouts 2×2, 3×3, 4×4, 6×6, 9×9 (paged; 6×6 and above show status tiles to keep the browser light). Analysis runs on the server, so closing the dashboard does not stop monitoring.
2. **Detection**: one asyncio task per camera, YOLOv8n + tracking rules over 2 s segments. Types: vehicle collision, traffic congestion, person fall, restricted-zone entry, abnormal motion.
3. **Evidence**: a rolling buffer gives 3 frames before, the detection frame, and 4 frames after (configurable). Each frame record has frame ID, camera, timestamp, incident ID, image, finding, confidence, video reference and SHA-256. The original video is hashed on upload and never modified.
4. **Report**: ID `INC-YYYY-NNN`, overview, summary, frame-by-frame evidence, footage player, AI findings with limitations, human review.
5. **Email**: incidents at `EMAIL_MIN_SEVERITY` or above send a summary plus `/incidents/<id>`. No footage attached. The link requires sign-in. Without `SMTP_HOST` the email is saved to `backend/data/outbox/`.
6. **Workbench** (`/incidents/<id>`): summary, evidence timeline with enlargeable frames, footage with jump and frame comparison, AI findings, reviewer actions with a reason (Confirm incident, Confirm emergency, Not an emergency, Request further review), audit trail, access log, integrity check.
7. **Dashboard update**: Confirm emergency puts the incident in the main panel with a voice alert, then the team moves it through New, Dispatched, On scene, Resolved.

Severity is an AI assessment. It orders the review queue and never dispatches anything.

## Not built yet
- Live RTSP/IP streams (OpenCV can open them; pacing and reconnect logic are not written). Uploaded files only for now.
- Fire/smoke and assault detection. They need a dedicated detector or action-recognition model.
- Saved video clips. The workbench plays the original footage and jumps to the timestamp instead.
- Brute-force protection on login, per-user roles, HTTPS. Add these before real deployment.

## Config (env)
`USERS`, `SECRET_KEY`, `PUBLIC_URL`, `SMTP_HOST/PORT/USER/PASSWORD/FROM`, `ALERT_EMAIL_TO`, `EMAIL_MIN_SEVERITY` (high), `EVIDENCE_PRE_SEC` (3), `EVIDENCE_POST_SEC` (4), `CONGESTION_MIN` (8), `SEGMENT_SEC`, `SAMPLE_FPS`, `ANALYSIS_SPEED` (2), `MAX_CAMERAS` (81), `YOLO_MODEL`, `MONGO_URL`, `ANTHROPIC_API_KEY` (Claude writes the summary from the evidence frame).
Restricted zones: `backend/zones.json` (normalised x1,y1,x2,y2 per camera id).
Tests: `cd backend && pytest tests`
