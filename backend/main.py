import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
from agents import now_iso, sha256_file
from config import ANALYSIS_SPEED, EVIDENCE_DIR, MAX_CAMERAS, PUBLIC_URL, UPLOAD_DIR, USERS
from orchestrator import Hub, Orchestrator
from store import Store

logging.basicConfig(level=logging.INFO)
if USERS.get("admin") == "admin123":
    logging.warning("Default login admin/admin123 is active. Set USERS before real use.")

app = FastAPI(title="Incident Desk")
store, hub = Store(), Hub()
orch = Orchestrator(store, hub)
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


class Login(BaseModel):
    user: str
    password: str


class Review(BaseModel):
    action: Literal["confirm_incident", "confirm_emergency", "not_emergency", "request_review"]
    reason: str = ""


class Patch(BaseModel):
    summary: Optional[str] = None
    response_status: Optional[Literal["new", "dispatched", "on_scene", "resolved"]] = None


@app.post("/api/login")
def login(body: Login, response: Response):
    if not auth.check_login(body.user, body.password):
        raise HTTPException(401, "Wrong user name or password")
    response.set_cookie(auth.COOKIE, auth.make_token(body.user), httponly=True, samesite="lax",
                        max_age=auth.TTL, secure=PUBLIC_URL.startswith("https"))
    return dict(user=body.user)


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return dict(ok=True)


@app.get("/api/me")
def me(user: str = Depends(auth.current_user)):
    return dict(user=user)


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), user: str = Depends(auth.current_user)):
    if len(store.cameras) + len(files) > MAX_CAMERAS:
        raise HTTPException(400, f"At most {MAX_CAMERAS} cameras per session. Start over to clear them.")
    for f in files:
        if Path(f.filename or "").suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, f"{f.filename}: unsupported file type")
    added = []
    for f in files:
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{Path(f.filename).name}"
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        added.append(await orch.add_video(dest, f.filename))
    return dict(cameras=added)


@app.get("/api/state")
def state(user: str = Depends(auth.current_user)):
    return orch.snapshot()


@app.get("/api/agents")
def get_agents(user: str = Depends(auth.current_user)):
    return orch.get_agents_info()


@app.get("/api/videos/{cam_id}")
def video(cam_id: str, user: str = Depends(auth.current_user)):
    path = store.paths.get(cam_id)
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/evidence/{inc_id}/{name}")
def evidence(inc_id: str, name: str, user: str = Depends(auth.current_user)):
    if not re.fullmatch(r"INC-\d{4}-\d+", inc_id) or not re.fullmatch(r"F\d+\.jpg", name):
        raise HTTPException(404)
    f = EVIDENCE_DIR / inc_id / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/jpeg")


@app.get("/api/incidents/{inc_id}")
def get_incident(inc_id: str, user: str = Depends(auth.current_user)):
    inc = store.incidents.get(inc_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    inc["access_log"] = (inc["access_log"] + [dict(at=now_iso(), user=user)])[-100:]
    store.save_incident(inc)
    return inc


@app.get("/api/incidents/{inc_id}/integrity")
async def integrity(inc_id: str, user: str = Depends(auth.current_user)):
    inc = store.incidents.get(inc_id)
    if not inc:
        raise HTTPException(404)
    path = store.paths.get(inc["camera_id"])
    video_ok = bool(path) and await asyncio.to_thread(sha256_file, path) == inc["video_ref"]["sha256"]
    frames = []
    for fr in inc["evidence"]:
        f = EVIDENCE_DIR / inc_id / fr["filename"]
        frames.append(dict(frame_id=fr["frame_id"], ok=f.is_file() and sha256_file(f) == fr["sha256"]))
    return dict(video_ok=video_ok, frames=frames, checked_at=now_iso())


@app.post("/api/incidents/{inc_id}/review")
async def review(inc_id: str, body: Review, user: str = Depends(auth.current_user)):
    inc = await orch.review(inc_id, body.action, body.reason.strip(), user)
    if not inc:
        raise HTTPException(404)
    return inc


@app.patch("/api/incidents/{inc_id}")
async def patch_incident(inc_id: str, body: Patch, user: str = Depends(auth.current_user)):
    try:
        inc = await orch.update_incident(inc_id, body.model_dump(exclude_none=True), user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not inc:
        raise HTTPException(404)
    return inc


@app.post("/api/reset")
async def reset(user: str = Depends(auth.current_user)):
    await orch.reset()
    return dict(ok=True)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    hub.clients.add(sock)
    try:
        await sock.send_json(orch.snapshot())
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(sock)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return {"status": "ok"}


DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        f = (DIST / full_path).resolve()
        if full_path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")  # deep links such as /incidents/INC-2026-001
