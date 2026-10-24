"""Video Analysis Agent: sampled-frame detection plus rule-based incident heuristics.

Everything here describes *possible* events. Rules look at bounding-box geometry and
motion only, so the wording downstream must stay hedged and operator-verified.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

from config import (CONGESTION_MIN, EVENT_COOLDOWN_SEC, EVIDENCE_POST_SEC, EVIDENCE_PRE_SEC,
                    SAMPLE_FPS, SEGMENT_SEC, YOLO_CONF, YOLO_IMGSZ, YOLO_MODEL)

log = logging.getLogger("analysis")

PERSON = 0
VEHICLES = {2, 3, 5, 7}  # car, motorcycle, bus, truck (COCO ids)
NAMES = {0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


@dataclass
class Det:
    cls: int
    box: tuple
    conf: float


def iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


class Detector:
    """One shared YOLOv8 model, guarded by a lock so camera threads can share it."""

    _model = None
    _tried = False
    _lock = threading.Lock()

    @classmethod
    def model(cls):
        with cls._lock:
            if not cls._tried:
                cls._tried = True
                try:
                    try:
                        import torch
                        if hasattr(torch, "set_num_threads") and torch.get_num_threads() > 2:
                            torch.set_num_threads(2)
                    except Exception:
                        pass
                    from ultralytics import YOLO

                    cls._model = YOLO(YOLO_MODEL)
                except Exception as exc:  # noqa: BLE001
                    log.warning("YOLO unavailable (%s); using motion-only analysis", exc)
        return cls._model

    @classmethod
    def detect(cls, frame):
        m = cls.model()
        if m is None:
            return None
        with cls._lock:
            res = m.predict(frame, conf=YOLO_CONF, verbose=False, imgsz=YOLO_IMGSZ, classes=[0, 2, 3, 5, 7], max_det=20)[0]
        out = []
        for b in res.boxes:
            c = int(b.cls[0])
            if c == PERSON or c in VEHICLES:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                out.append(Det(c, (x1, y1, x2, y2), float(b.conf[0])))
        return out


class Track:
    def __init__(self, tid, cls, box, t):
        self.id, self.cls, self.first_t = tid, cls, t
        self.hist = [(t, box)]
        self.missed = 0
        self.zone_since = None
        self.zone_fired = False

    @property
    def box(self):
        return self.hist[-1][1]

    def add(self, t, box):
        self.hist.append((t, box))
        del self.hist[:-60]
        self.missed = 0

    def at(self, t):
        return min(self.hist, key=lambda e: abs(e[0] - t))

    def speed(self, t0, t1, diag):
        """Centre displacement in frame-diagonals per second between two times."""
        a, b = self.at(t0), self.at(t1)
        dt = b[0] - a[0]
        if dt < 0.2:
            if len(self.hist) >= 2:
                a, b = self.hist[0], self.hist[-1]
                dt = b[0] - a[0]
            if dt < 0.2:
                return 0.0
        return math.dist(center(a[1]), center(b[1])) / dt / diag


class Tracker:
    def __init__(self):
        self.tracks: dict[int, Track] = {}
        self.next_id = 1

    def update(self, dets, t):
        free = list(dets)
        for tr in list(self.tracks.values()):
            best, best_d = None, 1e9
            tw, th = tr.box[2] - tr.box[0], tr.box[3] - tr.box[1]
            for d in free:
                if d.cls != tr.cls:
                    continue
                dist = math.dist(center(tr.box), center(d.box))
                if (iou(tr.box, d.box) > 0.1 or dist < 0.6 * max(tw, th)) and dist < best_d:
                    best, best_d = d, dist
            if best:
                tr.add(t, best.box)
                free.remove(best)
            else:
                tr.missed += 1
        for d in free:
            self.tracks[self.next_id] = Track(self.next_id, d.cls, d.box, t)
            self.next_id += 1
        for tid in [k for k, v in self.tracks.items() if v.missed > 5]:
            del self.tracks[tid]
        return list(self.tracks.values())


class RuleEngine:
    """Turns tracks into candidate events: collision, fall, restricted-zone entry."""

    def __init__(self, zone=None):
        self.tracker = Tracker()
        self.zone = zone
        self.summary = dict(vehicles=None, persons=None, conf=None)
        self.cong_since, self.cong_fired = None, False

    def step(self, dets, t, shape):
        h, w = shape
        diag = math.hypot(w, h)
        tracks = [tr for tr in self.tracker.update(dets, t) if tr.missed == 0]
        self.summary = dict(
            vehicles=sum(tr.cls in VEHICLES for tr in tracks), persons=sum(tr.cls == PERSON for tr in tracks),
            conf=round(float(np.mean([d.conf for d in dets])), 2) if dets else None,
        )
        events = []

        vehicles = [tr for tr in tracks if tr.cls in VEHICLES and t - tr.first_t >= 1.0]
        for a, b in combinations(vehicles, 2):
            ov = iou(a.box, b.box)
            if ov < 0.08:
                continue
            drop = False
            for tr in (a, b):
                before, now = tr.speed(t - 1.6, t - 0.6, diag), tr.speed(t - 0.6, t, diag)
                if before > 0.08 and now < 0.4 * before:
                    drop = True
            if drop:
                events.append(
                    dict(
                        type="collision", t=t, confidence=min(0.9, 0.55 + ov),
                        boxes=[(a.box, NAMES[a.cls]), (b.box, NAMES[b.cls])],
                        detail=(f"A {NAMES[a.cls]} and a {NAMES[b.cls]} overlap in the frame and at least "
                                "one slowed sharply just before the overlap."),
                    )
                )

        for tr in tracks:
            if tr.cls != PERSON:
                continue
            bw, bh = tr.box[2] - tr.box[0], tr.box[3] - tr.box[1]
            if t - tr.first_t >= 1.5 and bh > 0:
                past = [e for e in tr.hist if t - 2.0 <= e[0] <= t - 0.8]
                if len(past) >= 2:
                    ars = [(e[1][2] - e[1][0]) / max(1.0, e[1][3] - e[1][1]) for e in past]
                    hs = [e[1][3] - e[1][1] for e in past]
                    if np.mean(ars) < 0.9 and bw / bh > 1.1 and bh < 0.75 * np.mean(hs):
                        events.append(
                            dict(
                                type="fall", t=t, confidence=0.6,
                                boxes=[(tr.box, "person")],
                                detail="A person's bounding box changed from upright to wide and short within about a second.",
                            )
                        )
            if self.zone:
                x1, y1, x2, y2 = self.zone
                fx, fy = (tr.box[0] + tr.box[2]) / 2 / w, tr.box[3] / h
                if x1 <= fx <= x2 and y1 <= fy <= y2:
                    tr.zone_since = tr.zone_since if tr.zone_since is not None else t
                    if t - tr.zone_since >= 1.0 and not tr.zone_fired:
                        tr.zone_fired = True
                        events.append(
                            dict(
                                type="restricted_zone", t=t, confidence=0.7,
                                boxes=[(tr.box, "person")],
                                detail="A person has stayed inside the area marked as restricted for this camera.",
                            )
                        )
                else:
                    tr.zone_since = None

        veh_all = [tr for tr in tracks if tr.cls in VEHICLES]
        slow = [tr for tr in veh_all if tr.speed(t - 1.5, t, diag) < 0.02]
        if len(veh_all) >= CONGESTION_MIN and len(slow) >= 0.8 * len(veh_all):
            self.cong_since = t if self.cong_since is None else self.cong_since
            if t - self.cong_since >= 4.0 and not self.cong_fired:
                self.cong_fired = True
                events.append(
                    dict(
                        type="congestion", t=t, confidence=0.65, boxes=[(tr.box, NAMES[tr.cls]) for tr in slow[:12]],
                        detail=f"{len(slow)} of {len(veh_all)} tracked vehicles have been nearly stationary for several seconds.",
                    )
                )
        else:
            self.cong_since, self.cong_fired = None, False
        return events


class MotionMonitor:
    """Detector-independent fallback: flags sudden, sustained frame-wide change."""

    def __init__(self):
        self.prev, self.base, self.hot = None, 0.01, 0

    def update(self, frame, t):
        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self.prev is None:
            self.prev = g
            return None
        frac = float((cv2.absdiff(g, self.prev) > 25).mean())
        self.prev = g
        surge = frac > 0.12 and frac > 4 * self.base
        self.hot = self.hot + 1 if surge else 0
        if not surge:
            self.base = 0.95 * self.base + 0.05 * frac
        if self.hot >= 2:
            self.hot = 0
            return dict(
                type="motion_surge", t=t, confidence=min(0.85, 0.45 + frac), boxes=[],
                detail=f"About {frac * 100:.0f}% of the frame changed at once, well above this camera's normal level.",
            )
        return None


class LazyRec(dict):
    """Encodes JPEG only on demand when evidence is saved, skipping unused buffer frames."""

    def __init__(self, t, frame, summary, jpeg_fn):
        super().__init__(t=t, summary=summary)
        self.frame = frame
        self._jpeg_fn = jpeg_fn

    def __getitem__(self, key):
        if key == "jpeg":
            if "jpeg" not in self:
                self["jpeg"] = self._jpeg_fn(self.frame)
            return super().__getitem__("jpeg")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "jpeg":
            return self["jpeg"]
        return super().get(key, default)


class VideoAnalyzer:
    """Samples frames, keeps a rolling buffer, and collects before/during/after evidence per event."""

    def __init__(self, path, zone=None):
        self.path = str(path)
        self.engine = RuleEngine(zone)
        self.motion = MotionMonitor()
        self.last: dict[str, float] = {}
        self.use_yolo = False
        self.buffer: deque = deque()   # rolling buffer of frames
        self.pending: list[dict] = []  # events still collecting post-event frames
        self.completed: list[dict] = []

    @staticmethod
    def _jpeg(img, q=78) -> bytes:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        return buf.tobytes() if ok else b""

    def _annotated(self, frame, ev, t) -> bytes:
        img = frame.copy()
        for box, label in ev["boxes"]:
            p1, p2 = (int(box[0]), int(box[1])), (int(box[2]), int(box[3]))
            cv2.rectangle(img, p1, p2, (60, 60, 235), 2)
            cv2.putText(img, label, (p1[0], max(14, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 235), 1)
        cv2.putText(img, fmt_ts(t), (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        return self._jpeg(img, 82)

    def _pick_pre(self, t):
        chosen = []
        cands = [b for b in self.buffer if b["t"] <= t - 0.3]
        for k in range(EVIDENCE_PRE_SEC, 0, -1):
            if not cands:
                break
            best = min(cands, key=lambda b: abs(b["t"] - (t - k)))
            if all(best is not c for c in chosen):
                chosen.append(best)
        return chosen

    def _process(self, frame, t):
        h, w = frame.shape[:2]
        if w > 640:
            frame = cv2.resize(frame, (640, int(h * 640 / w)))
        events = []
        dets = Detector.detect(frame) if self.use_yolo else None
        if dets is not None:
            events += self.engine.step(dets, t, frame.shape[:2])
            summary = self.engine.summary
        else:
            summary = dict(vehicles=None, persons=None, conf=None)
        m = self.motion.update(frame, t)
        if m:
            events.append(m)
        rec = LazyRec(t=t, frame=frame, summary=summary, jpeg_fn=lambda f: self._jpeg(f, 75))

        for p in list(self.pending):  # post-event evidence, one frame per second
            if t - p["last_t"] >= 1.0 and len(p["post"]) < EVIDENCE_POST_SEC:
                p["post"].append(rec)
                p["last_t"] = t
            if len(p["post"]) >= EVIDENCE_POST_SEC:
                self.completed.append(dict(key=p["key"], frames=p["post"]))
                self.pending.remove(p)

        out = []
        for ev in events:
            if t - self.last.get(ev["type"], -1e9) < EVENT_COOLDOWN_SEC:
                continue
            self.last[ev["type"]] = t
            ev["key"] = uuid.uuid4().hex
            ev["jpeg"] = self._annotated(frame, ev, t)
            ev["frames"] = dict(pre=self._pick_pre(t), event=dict(t=t, jpeg=ev["jpeg"], summary=summary))
            self.pending.append(dict(key=ev["key"], last_t=t, post=[]))
            out.append(ev)

        self.buffer.append(rec)
        while self.buffer and t - self.buffer[0]["t"] > EVIDENCE_PRE_SEC + 0.5:
            self.buffer.popleft()
        return out

    def _take_post(self):
        done, self.completed = self.completed, []
        return done

    def segments(self):
        """Yield one dict per SEGMENT_SEC of video. Blocking; run it in a worker thread."""
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise RuntimeError("Could not open video file")
        try:
            self.use_yolo = Detector.model() is not None
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            duration = total / fps if total > 0 else 0.0
            step = max(1, round(fps / SAMPLE_FPS))
            idx, seg_start, seg_end, events = 0, 0.0, SEGMENT_SEC, []
            t = 0.0
            while cap.grab():
                t = idx / fps
                if idx % step == 0:
                    ok, frame = cap.retrieve()
                    if ok:
                        events += self._process(frame, t)
                idx += 1
                if t >= seg_end:
                    yield dict(t_start=seg_start, t_end=t, events=events, post=self._take_post(),
                               progress=min(0.99, t / duration) if duration else 0.0, duration=duration)
                    seg_start, seg_end, events = t, seg_end + SEGMENT_SEC, []
            for p in self.pending:  # video ended: close out whatever post-event frames exist
                self.completed.append(dict(key=p["key"], frames=p["post"]))
            self.pending.clear()
            yield dict(t_start=seg_start, t_end=t, events=events, post=self._take_post(),
                       progress=1.0, duration=duration or t)
        finally:
            cap.release()
