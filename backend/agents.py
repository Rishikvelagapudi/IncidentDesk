"""Logical agents. They share one vision pipeline; the orchestrator coordinates them."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone

import httpx

from analysis import VideoAnalyzer, fmt_ts
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

log = logging.getLogger("agents")

SEV_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}

INCIDENT_TYPES = {
    "collision": dict(
        label="Possible vehicle collision", short="Road Accident", category="Road safety", severity="critical",
        action="Review the footage and verify whether emergency assistance is required.",
        procedure=["Confirm the collision in the workbench", "Check for people on the ground or trapped", "Confirm the emergency to alert the control room"],
        limits=["Detection uses vehicle box overlap and sudden slowing. It cannot confirm that the vehicles touched.",
                "Injuries and damage cannot be determined from these frames.",
                "Occlusion or camera angle can create false overlaps."],
    ),
    "congestion": dict(
        label="Possible traffic congestion", short="Traffic Congestion", category="Road safety", severity="medium",
        action="Check whether traffic is blocked and whether a cause is visible.",
        procedure=["Review the frames for a blockage or breakdown", "Notify traffic control if the road is blocked"],
        limits=["Based on the number of slow-moving vehicles. Parked vehicles can look the same.",
                "The cause of the slowdown is not determined."],
    ),
    "fall": dict(
        label="Possible person fall", short="Person Fall", category="Public safety", severity="high",
        action="Review the footage and check whether the person gets up or needs help.",
        procedure=["Confirm the fall in the workbench", "Send on-site staff if the person stays down", "Confirm the emergency if there are signs of injury"],
        limits=["Based on the person's bounding box changing shape. Sitting, bending or crouching can look similar.",
                "The cause of the fall and any injury cannot be determined."],
    ),
    "restricted_zone": dict(
        label="Possible unauthorized entry", short="Restricted-Zone Entry", category="Industrial safety", severity="high",
        action="Verify the person's identity or authorization before escalating.",
        procedure=["Check the access roster", "Contact on-site security", "Record the outcome in the review reason"],
        limits=["The zone is a configured rectangle. The system cannot tell whether the person is authorized.",
                "Identity is not determined."],
    ),
    "motion_surge": dict(
        label="Sudden abnormal motion", short="Abnormal Motion", category="General", severity="medium",
        action="Review the footage to find out what changed.",
        procedure=["Review the frames", "Escalate if people or vehicles appear at risk"],
        limits=["Frame-wide change can come from lighting, camera shake or weather rather than a real event.",
                "This check does not identify objects or people."],
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def counts_text(s: dict) -> str:
    if s.get("vehicles") is None:
        return "No object-detector output for this camera (motion analysis only)."
    return f"Tracked: {s['vehicles']} vehicle(s), {s['persons']} person(s)."


class VideoAnalysisAgent:
    def open(self, path, zone=None) -> VideoAnalyzer:
        return VideoAnalyzer(path, zone)


class IncidentDetectionAgent:
    MIN_CONF = 0.45

    def classify(self, ev: dict) -> dict | None:
        spec = INCIDENT_TYPES.get(ev["type"])
        if not spec or ev["confidence"] < self.MIN_CONF:
            return None
        return dict(type=ev["type"], label=spec["label"], short=spec["short"], category=spec["category"],
                    severity=spec["severity"], detail=ev["detail"], confidence=round(ev["confidence"], 2),
                    recommended_action=spec["action"])


class PriorityAlertAgent:
    """Severity is an AI assessment only. It orders the review queue; it never dispatches anything."""

    def prioritize(self, cam: dict, inc: dict, camera_history: list[dict]) -> dict:
        inc["severity_note"] = "AI assessment only. A human reviewer decides whether this is an emergency."
        if inc["type"] == "fall":
            for other in camera_history:
                if other["type"] == "collision" and abs(other["video_ts"] - inc["video_ts"]) <= 15:
                    inc["severity"] = "critical"
                    inc["severity_note"] += " Raised because it follows a possible collision on this camera."
        inc["priority_score"] = SEV_RANK[inc["severity"]] * 100 + int(inc["confidence"] * 100)
        spoken = f"{int(inc['video_ts'] // 60)} minutes {int(inc['video_ts'] % 60)} seconds"
        cam_num = cam["id"].split("-")[-1]
        cam_spoken = cam_num if not cam_num.isdigit() else str(int(cam_num))
        inc["alert_text"] = (
            f"Emergency confirmed. Camera {cam_spoken}, {inc['label'].lower()} at {spoken}."
        )
        return inc


class InvestigationAgent:
    def investigate(self, store, inc: dict) -> dict:
        similar = [
            dict(id=o["id"], camera_name=o["camera_name"], video_ts_text=o["video_ts_text"], emergency=o.get("emergency"))
            for o in store.incidents.values()
            if o["type"] == inc["type"] and o["review_status"] == "confirmed" and o["id"] != inc["id"]
        ][:3]
        same_cam = sorted(
            [o for o in store.incidents_for(inc["camera_id"]) if o["id"] != inc["id"]] + [inc],
            key=lambda o: o["video_ts"]
        )
        inc["similar_incidents"] = similar
        inc["timeline"] = [dict(id=o["id"], video_ts_text=o["video_ts_text"], label=o["label"]) for o in same_cam]
        inc["procedure"] = INCIDENT_TYPES[inc["type"]]["procedure"]
        return inc


class ReportingAgent:
    def draft(self, inc: dict, cam: dict, ev: dict, wall_s: float) -> dict:
        spec = INCIDENT_TYPES[inc["type"]]
        event_summary = ev.get("frames", {}).get("event", {}).get("summary", {}) if "frames" in ev else {}
        counts = counts_text(event_summary)
        method = (
            "Frame-difference motion check"
            if ev.get("type") == "motion_surge" or event_summary.get("vehicles") is None
            else "YOLOv8 detection with tracking rules"
        )
        inc.update(
            camera_id=cam["id"], camera_name=cam["name"], location=cam["name"], video_ts=round(ev["t"], 1),
            video_ts_text=fmt_ts(ev["t"]), detected_at=now_iso(), analysis_wall_s=round(wall_s, 1),
            review_status="awaiting_review", emergency=None, response_status=None, description_source="rules",
            summary=(f"A {inc['label'].lower()} was detected by {cam['id']} ({cam['name']}) at video time {fmt_ts(ev['t'])}. "
                     f"{inc['detail']} The system saved frames from before, during and after the event for human review. "
                     "The incident is awaiting confirmation."),
            findings=[dict(event=inc["label"], timestamp=fmt_ts(ev["t"]), confidence=inc["confidence"],
                           evidence=inc["detail"], objects=counts,
                           method=method)],
            limitations=list(spec["limits"]), evidence=[], evidence_complete=False, reviews=[], access_log=[],
            email=dict(status="pending"), video_ref=dict(camera_id=cam["id"], file=cam.get("file"), sha256=cam.get("video_sha256")),
            audit=[dict(at=now_iso(), actor="agent", action="Incident report created")],
        )
        return inc

    @staticmethod
    def frame_text(phase: str, ev: dict | None, summary: dict) -> str:
        if phase == "event":
            return f"Detection finding: {ev['detail']} {counts_text(summary)}"
        return f"{'Before' if phase == 'pre' else 'After'} the event. {counts_text(summary)}"

    def frame_records(self, inc: dict, ev: dict | None, frames: list[tuple[str, dict]], start: int):
        """frames: [(phase, {t, jpeg, summary})] -> (metadata records, [(filename, bytes)])."""
        records, files = [], []
        for n, (phase, f) in enumerate(frames, start=start):
            name = f"F{n:02d}.jpg"
            conf = inc["confidence"] if phase == "event" else f["summary"].get("conf")
            records.append(dict(
                frame_id=f"{inc['id']}-F{n:02d}", incident_id=inc["id"], camera_id=inc["camera_id"], phase=phase,
                video_ts=round(f["t"], 1), video_ts_text=fmt_ts(f["t"]), filename=name,
                url=f"/api/evidence/{inc['id']}/{name}", finding=self.frame_text(phase, ev, f["summary"]),
                confidence=conf, video_ref=dict(file=inc["video_ref"]["file"], at=round(f["t"], 1)),
                sha256=sha256_bytes(f["jpeg"]),
            ))
            files.append((name, f["jpeg"]))
        return records, files

    async def enrich(self, inc: dict, jpeg: bytes) -> dict | None:
        """Optional: Claude rewrites the summary from the evidence frame. Falls back silently."""
        if not ANTHROPIC_API_KEY or not jpeg:
            return None
        prompt = (
            "You assist a CCTV control room. A rule-based detector flagged this frame as: "
            f"{inc['label']} (camera {inc['camera_name']}, video time {inc['video_ts_text']}). "
            "Return JSON only with keys summary (2-3 sentences describing only what is visible in the frame and what the detector flagged) "
            "and uncertainties (a list of 1-3 short strings naming what cannot be determined from this frame). "
            "Do not claim a crime or an injury unless clearly visible."
        )
        body = dict(model=ANTHROPIC_MODEL, max_tokens=500, messages=[dict(role="user", content=[
            dict(type="image", source=dict(type="base64", media_type="image/jpeg", data=base64.b64encode(jpeg).decode())),
            dict(type="text", text=prompt)])])
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.post("https://api.anthropic.com/v1/messages",
                                      headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"}, json=body)
                r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"]).strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(text)
            return dict(summary=str(data["summary"]), extra_limits=[str(x) for x in data["uncertainties"]][:3],
                        description_source="claude")
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude description skipped: %s", exc)
            return None


def get_agent_specs() -> list[dict]:
    return [
        dict(
            id="video_analysis",
            name="VideoAnalysisAgent",
            role="Vision & Ingestion Pipeline",
            status="active",
            method="YOLOv8 + Optical Frame Difference",
            description="Extracts video frames, samples at target FPS, maintains rolling pre-buffer, and tracks bounding boxes across frames.",
        ),
        dict(
            id="incident_detection",
            name="IncidentDetectionAgent",
            role="Heuristic & Anomaly Classifier",
            status="active",
            method="Multi-class Geometry & Spatio-temporal Rules",
            description="Classifies collisions, falls, restricted zone intrusions, traffic congestions, and abnormal motion surges.",
        ),
        dict(
            id="priority_alert",
            name="PriorityAlertAgent",
            role="Dynamic Triage & Alerting",
            status="active",
            method="Context-correlated Priority Matrix",
            description="Calculates real-time priority score, elevates severity on compound events, and prepares spoken voice alerts.",
        ),
        dict(
            id="investigation",
            name="InvestigationAgent",
            role="Correlation & SOP Investigator",
            status="active",
            method="Cross-incident Query & Protocol Retrieval",
            description="Retrieves matching historical incidents, constructs camera timeline, and provides standard operating procedures.",
        ),
        dict(
            id="reporting",
            name="ReportingAgent",
            role="Forensic & Evidence Synthesizer",
            status="active",
            method="SHA-256 Cryptographic Chain + Optional Vision LLM",
            description="Assembles pre/event/post evidence frames, computes tamper-evident cryptographic hashes, and drafts incident reports.",
        ),
    ]
