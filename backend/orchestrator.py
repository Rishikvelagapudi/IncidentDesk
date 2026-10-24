"""Orchestrator: one asyncio task per camera, all feeding one shared review queue.

Flow: detect -> evidence -> report -> email alert -> human review -> emergency confirmed -> dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import notifier
from agents import (
    SEV_RANK, IncidentDetectionAgent, InvestigationAgent, PriorityAlertAgent,
    ReportingAgent, VideoAnalysisAgent, get_agent_specs, now_iso, sha256_file,
)
from config import ANALYSIS_SPEED, EMAIL_MIN_SEVERITY, EVIDENCE_DIR, load_zones

log = logging.getLogger("orchestrator")


def is_open(inc: dict) -> bool:
    return not (inc["review_status"] == "dismissed" or inc.get("emergency") is False or inc.get("response_status") == "resolved")


class Hub:
    def __init__(self):
        self.clients = set()

    async def broadcast(self, msg: dict):
        for c in list(self.clients):
            try:
                await c.send_json(msg)
            except Exception:  # noqa: BLE001
                self.clients.discard(c)


class Orchestrator:
    def __init__(self, store, hub: Hub):
        self.store, self.hub = store, hub
        self.tasks: dict[str, asyncio.Task] = {}
        self.keys: dict[str, str] = {}  # event key -> incident id, for post-event frames
        self.video = VideoAnalysisAgent()
        self.detect = IncidentDetectionAgent()
        self.priority = PriorityAlertAgent()
        self.investigate = InvestigationAgent()
        self.reporting = ReportingAgent()

    def get_agents_info(self) -> list[dict]:
        specs = get_agent_specs()
        active_cams = len([c for c in self.store.cameras.values() if c.get("status") == "analyzing"])
        for s in specs:
            s["active_cameras"] = active_cams
            s["total_incidents"] = len(self.store.incidents)
        return specs

    def snapshot(self) -> dict:
        return dict(
            type="snapshot",
            cameras=list(self.store.cameras.values()),
            incidents=list(self.store.incidents.values()),
            agents=self.get_agents_info(),
        )

    async def add_video(self, path: Path, original_name: str) -> dict:
        cam_id = f"CAM-{len(self.store.cameras) + 1:02d}"
        sha = await asyncio.to_thread(sha256_file, path)
        cam = dict(id=cam_id, name=Path(original_name).stem[:40], file=original_name, video_sha256=sha, status="queued",
                   progress=0.0, video_ts=0.0, video_url=f"/api/videos/{cam_id}", severity="normal", incident_count=0, error=None)
        self.store.cameras[cam_id] = cam
        self.store.paths[cam_id] = str(path)
        await self.hub.broadcast(dict(type="camera_update", camera=cam))
        self.tasks[cam_id] = asyncio.create_task(self.run_camera(cam_id))
        return cam

    async def run_camera(self, cam_id: str):
        cam = self.store.cameras[cam_id]
        zones = load_zones()
        zone = zones.get(cam_id) or zones.get(Path(self.store.paths[cam_id]).stem.split("_", 1)[-1])
        started = time.monotonic()
        try:
            cam["status"] = "analyzing"
            await self.hub.broadcast(dict(type="camera_update", camera=cam))
            gen = self.video.open(self.store.paths[cam_id], zone).segments()
            while True:
                t0 = time.monotonic()
                seg = await asyncio.to_thread(next, gen, None)  # heavy work off the event loop
                if seg is None:
                    break
                cam["progress"], cam["video_ts"] = round(seg["progress"], 3), round(seg["t_end"], 1)
                for ev in seg["events"]:
                    await self.handle_event(cam, ev, time.monotonic() - started)
                for post in seg["post"]:
                    await self.handle_post(post)
                await self.hub.broadcast(dict(type="camera_update", camera=cam))
                pace = (seg["t_end"] - seg["t_start"]) / ANALYSIS_SPEED
                await asyncio.sleep(max(0.0, pace - (time.monotonic() - t0)))
            cam["status"], cam["progress"] = "complete", 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Camera %s failed", cam_id)
            cam["status"], cam["error"] = "error", str(exc)
        await self.hub.broadcast(dict(type="camera_update", camera=cam))

    def _write(self, inc_id: str, files):
        d = EVIDENCE_DIR / inc_id
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (d / name).write_bytes(data)

    async def handle_event(self, cam: dict, ev: dict, wall_s: float):
        cand = self.detect.classify(ev)
        if not cand:
            return
        cand["id"] = self.store.next_id()
        cand["video_ts"] = round(ev["t"], 1)
        inc = self.priority.prioritize(cam, cand, self.store.incidents_for(cam["id"]))
        inc = self.reporting.draft(inc, cam, ev, wall_s)
        inc = self.investigate.investigate(self.store, inc)
        frames = [("pre", f) for f in ev["frames"]["pre"]] + [("event", ev["frames"]["event"])]
        inc["evidence"], files = self.reporting.frame_records(inc, ev, frames, 1)
        self._write(inc["id"], files)
        self.keys[ev["key"]] = inc["id"]
        self.store.add_incident(inc)
        self.refresh_camera(cam)
        await self.hub.broadcast(dict(type="incident_new", incident=inc))
        if SEV_RANK[inc["severity"]] >= SEV_RANK[EMAIL_MIN_SEVERITY]:
            asyncio.create_task(self.send_alert(inc))
        else:
            inc["email"] = dict(status="skipped", detail=f"Below the {EMAIL_MIN_SEVERITY} email threshold")
        asyncio.create_task(self.enrich(inc, ev["jpeg"]))

    async def handle_post(self, post: dict):
        inc = self.store.incidents.get(self.keys.pop(post["key"], ""))
        if not inc:
            return
        recs, files = self.reporting.frame_records(inc, None, [("post", f) for f in post["frames"]], len(inc["evidence"]) + 1)
        self._write(inc["id"], files)
        inc["evidence"] += recs
        inc["evidence_complete"] = True
        self.store.save_incident(inc)
        await self.hub.broadcast(dict(type="incident_update", incident=inc))

    async def send_alert(self, inc: dict):
        inc["email"] = await asyncio.to_thread(notifier.send, inc)
        inc["audit"].append(dict(at=now_iso(), actor="system", action=f"Email alert {inc['email']['status']}"))
        self.store.save_incident(inc)
        await self.hub.broadcast(dict(type="incident_update", incident=inc))

    async def enrich(self, inc: dict, jpeg: bytes):
        changes = await self.reporting.enrich(inc, jpeg)
        if changes and inc["review_status"] == "awaiting_review" and inc["description_source"] == "rules":
            inc["summary"] = changes["summary"]
            inc["limitations"] += changes["extra_limits"]
            inc["description_source"] = changes["description_source"]
            self.store.save_incident(inc)
            await self.hub.broadcast(dict(type="incident_update", incident=inc))

    def refresh_camera(self, cam: dict):
        open_inc = [i for i in self.store.incidents_for(cam["id"]) if is_open(i)]
        cam["incident_count"] = len(open_inc)
        cam["severity"] = max((i["severity"] for i in open_inc), key=lambda s: SEV_RANK[s], default="normal")

    async def _publish(self, inc: dict):
        self.store.save_incident(inc)
        cam = self.store.cameras.get(inc["camera_id"])
        if cam:
            self.refresh_camera(cam)
            await self.hub.broadcast(dict(type="camera_update", camera=cam))
        await self.hub.broadcast(dict(type="incident_update", incident=inc))

    async def review(self, inc_id: str, action: str, reason: str, user: str) -> dict | None:
        inc = self.store.incidents.get(inc_id)
        if not inc:
            return None
        was_emergency = inc.get("emergency") is True
        if action == "confirm_incident":
            inc["review_status"] = "confirmed"
        elif action == "confirm_emergency":
            inc.update(review_status="confirmed", emergency=True)
            inc["response_status"] = inc.get("response_status") or "new"
        elif action == "not_emergency":
            inc.update(review_status="dismissed", emergency=False, response_status=None)
        elif action == "request_review":
            inc["review_status"] = "escalated"
        inc["reviews"].append(dict(at=now_iso(), user=user, action=action, reason=reason))
        inc["audit"].append(dict(at=now_iso(), actor=user, action=f"Review: {action.replace('_', ' ')}" + (f" ({reason})" if reason else "")))
        await self._publish(inc)
        if inc["emergency"] and not was_emergency:
            await self.hub.broadcast(dict(type="emergency_confirmed", incident=inc))
        return inc

    async def update_incident(self, inc_id: str, patch: dict, user: str) -> dict | None:
        inc = self.store.incidents.get(inc_id)
        if not inc:
            return None
        if "response_status" in patch:
            if inc.get("emergency") is not True:
                raise ValueError("Response status applies only to confirmed emergencies")
            inc["audit"].append(dict(at=now_iso(), actor=user, action=f"Response status set to {patch['response_status']}"))
            inc["response_status"] = patch["response_status"]
        if "summary" in patch and patch["summary"] != inc["summary"]:
            inc["audit"].append(dict(at=now_iso(), actor=user, action="Edited summary"))
            inc["summary"], inc["description_source"] = patch["summary"], "reviewer"
        await self._publish(inc)
        return inc

    async def reset(self):
        for t in self.tasks.values():
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.keys.clear()
        self.store.clear()
        await self.hub.broadcast(self.snapshot())
