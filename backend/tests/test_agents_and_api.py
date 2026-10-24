import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import auth
from agents import (
    IncidentDetectionAgent,
    InvestigationAgent,
    PriorityAlertAgent,
    ReportingAgent,
    VideoAnalysisAgent,
    get_agent_specs,
    now_iso,
    sha256_bytes,
)
from main import app, orch, store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    res = client.post("/api/login", json={"user": "admin", "password": "admin123"})
    assert res.status_code == 200
    token = auth.make_token("admin")
    return {auth.COOKIE: token}


def test_agent_specs_metadata():
    specs = get_agent_specs()
    assert len(specs) == 5
    ids = {s["id"] for s in specs}
    assert ids == {
        "video_analysis",
        "incident_detection",
        "priority_alert",
        "investigation",
        "reporting",
    }
    for s in specs:
        assert s["status"] == "active"
        assert len(s["description"]) > 10


def test_incident_detection_agent():
    agent = IncidentDetectionAgent()
    # High confidence collision
    classified = agent.classify({
        "type": "collision",
        "confidence": 0.82,
        "detail": "Overlap detected between vehicle and car",
    })
    assert classified is not None
    assert classified["severity"] == "critical"
    assert classified["label"] == "Possible vehicle collision"
    assert classified["category"] == "Road safety"

    # Below MIN_CONF (0.45)
    low_conf = agent.classify({
        "type": "collision",
        "confidence": 0.3,
        "detail": "low confidence",
    })
    assert low_conf is None

    # Unknown type
    unknown = agent.classify({
        "type": "flying_saucer",
        "confidence": 0.99,
        "detail": "ufo",
    })
    assert unknown is None


def test_priority_alert_agent():
    agent = PriorityAlertAgent()
    cam = {"id": "CAM-03", "name": "North Gate"}

    # Fall alone
    inc = {
        "id": "INC-2026-001",
        "type": "fall",
        "severity": "high",
        "confidence": 0.75,
        "label": "Possible person fall",
        "video_ts": 125.0,
    }
    prioritized = agent.prioritize(cam, inc, [])
    assert prioritized["priority_score"] == 200 + 75
    assert "Camera 3" in prioritized["alert_text"]

    # Fall within 15 seconds of collision on same camera -> elevated to critical
    history = [{"type": "collision", "video_ts": 120.0}]
    prioritized_compound = agent.prioritize(cam, inc.copy(), history)
    assert prioritized_compound["severity"] == "critical"
    assert prioritized_compound["priority_score"] == 300 + 75
    assert "Raised because it follows a possible collision" in prioritized_compound["severity_note"]

    # Safe camera id handling for non-numeric camera ID
    cam_str = {"id": "CAM-NORTH-WEST", "name": "NW Perimeter"}
    prioritized_str = agent.prioritize(cam_str, inc.copy(), [])
    assert "WEST" in prioritized_str["alert_text"]


def test_investigation_agent():
    agent = InvestigationAgent()
    class MockStore:
        def __init__(self):
            self.incidents = {
                "INC-2026-001": {
                    "id": "INC-2026-001",
                    "camera_id": "CAM-01",
                    "camera_name": "Gate",
                    "type": "collision",
                    "review_status": "confirmed",
                    "emergency": True,
                    "video_ts": 10.0,
                    "video_ts_text": "00:00:10",
                    "label": "Road Accident",
                }
            }
        def incidents_for(self, cam_id):
            return [i for i in self.incidents.values() if i["camera_id"] == cam_id]

    mock_store = MockStore()
    new_inc = {
        "id": "INC-2026-002",
        "camera_id": "CAM-01",
        "camera_name": "Gate",
        "type": "collision",
        "video_ts": 35.0,
        "video_ts_text": "00:00:35",
        "label": "Road Accident",
    }
    investigated = agent.investigate(mock_store, new_inc)
    assert len(investigated["similar_incidents"]) == 1
    assert investigated["similar_incidents"][0]["id"] == "INC-2026-001"
    assert len(investigated["procedure"]) >= 2
    assert len(investigated["timeline"]) == 2  # old and new


def test_reporting_agent():
    agent = ReportingAgent()
    inc = {
        "id": "INC-2026-003",
        "type": "restricted_zone",
        "label": "Possible unauthorized entry",
        "detail": "Person stayed in restricted zone",
        "confidence": 0.8,
    }
    cam = {"id": "CAM-02", "name": "Warehouse East", "file": "cam02.mp4", "video_sha256": "abcdef123456"}
    ev = {
        "t": 42.5,
        "type": "restricted_zone",
        "detail": "Person stayed in restricted zone",
        "frames": {
            "pre": [],
            "event": {
                "summary": {"vehicles": 0, "persons": 1, "conf": 0.85},
                "jpeg": b"fakejpegdata",
            },
        },
    }
    drafted = agent.draft(inc, cam, ev, wall_s=12.4)
    assert drafted["camera_id"] == "CAM-02"
    assert drafted["video_ts"] == 42.5
    assert drafted["review_status"] == "awaiting_review"
    assert len(drafted["limitations"]) > 0
    assert drafted["video_ref"]["sha256"] == "abcdef123456"

    # Frame records
    frames = [("event", {"t": 42.5, "jpeg": b"frame_bytes_123", "summary": {"conf": 0.8}})]
    records, files = agent.frame_records(drafted, ev, frames, start=1)
    assert len(records) == 1
    assert records[0]["filename"] == "F01.jpg"
    assert records[0]["sha256"] == sha256_bytes(b"frame_bytes_123")
    assert files[0][1] == b"frame_bytes_123"


def test_video_analysis_agent():
    agent = VideoAnalysisAgent()
    analyzer = agent.open("dummy_path.mp4", zone=[0.1, 0.1, 0.9, 0.9])
    assert analyzer.path == "dummy_path.mp4"
    assert analyzer.engine.zone == [0.1, 0.1, 0.9, 0.9]


def test_auth_workflow():
    assert auth.check_login("admin", "admin123") is True
    assert auth.check_login("admin", "wrong_password") is False
    assert auth.check_login("nonexistent", "admin123") is False

    token = auth.make_token("admin")
    assert auth.read_token(token) == "admin"

    # Tampered token
    tampered = token[:-4] + "xxxx"
    assert auth.read_token(tampered) is None


def test_api_endpoints(client):
    # Direct access without login
    r = client.get("/api/state")
    assert r.status_code == 200
    assert "cameras" in r.json()
    assert "incidents" in r.json()
    assert "agents" in r.json()

    # Me without cookie defaults to Operator
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["user"] == "Operator"

    # Agents endpoint directly accessible
    r = client.get("/api/agents")
    assert r.status_code == 200
    agents_data = r.json()
    assert len(agents_data) == 5

    # Optional login token still works if provided
    token = auth.make_token("admin")
    r = client.get("/api/me", cookies={auth.COOKIE: token})
    assert r.status_code == 200
    assert r.json()["user"] == "admin"


def test_full_incident_lifecycle(client):
    import cv2
    import numpy as np

    # 1. Login
    login_res = client.post("/api/login", json={"user": "admin", "password": "admin123"})
    assert login_res.status_code == 200
    cookies = login_res.cookies

    # 2. Reset cleanly
    client.post("/api/reset", cookies=cookies)

    # 3. Create a synthetic test video
    temp_video_path = Path("backend/data/uploads/synthetic_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(temp_video_path), fourcc, 5.0, (160, 120))
    for i in range(15):
        frame = np.full((120, 160, 3), i * 15, dtype=np.uint8)
        vw.write(frame)
    vw.release()

    try:
        with open(temp_video_path, "rb") as f:
            upload_res = client.post(
                "/api/upload",
                files={"files": ("synthetic_test.mp4", f, "video/mp4")},
                cookies=cookies,
            )
        assert upload_res.status_code == 200
        cameras = upload_res.json()["cameras"]
        assert len(cameras) == 1
        cam_id = cameras[0]["id"]

        # Check video stream endpoint
        vid_res = client.get(f"/api/videos/{cam_id}", cookies=cookies)
        assert vid_res.status_code == 200

        # Create an incident manually to test review and integrity
        inc_id = store.next_id()
        cand = {
            "id": inc_id,
            "type": "collision",
            "label": "Possible vehicle collision",
            "short": "Road Accident",
            "category": "Road safety",
            "severity": "critical",
            "confidence": 0.88,
            "detail": "Test vehicle overlap",
            "video_ts": 1.0,
            "recommended_action": "Review footage",
        }
        cam_record = store.cameras[cam_id]
        inc = orch.priority.prioritize(cam_record, cand, [])
        ev = {
            "t": 1.0,
            "type": "collision",
            "detail": "Test vehicle overlap",
            "boxes": [],
            "frames": {
                "pre": [],
                "event": {
                    "t": 1.0,
                    "jpeg": b"fakejpeg",
                    "summary": {"vehicles": 2, "persons": 0, "conf": 0.88},
                },
            },
        }
        inc = orch.reporting.draft(inc, cam_record, ev, 1.0)
        inc = orch.investigate.investigate(store, inc)
        inc["evidence"], files = orch.reporting.frame_records(inc, ev, [("event", ev["frames"]["event"])], 1)
        orch._write(inc["id"], files)
        store.add_incident(inc)

        # 4. Fetch incident
        get_res = client.get(f"/api/incidents/{inc_id}", cookies=cookies)
        assert get_res.status_code == 200
        assert get_res.json()["id"] == inc_id

        # 5. Integrity check
        int_res = client.get(f"/api/incidents/{inc_id}/integrity", cookies=cookies)
        assert int_res.status_code == 200
        assert "video_ok" in int_res.json()
        assert "frames" in int_res.json()

        # 6. Human Review: Confirm emergency
        rev_res = client.post(
            f"/api/incidents/{inc_id}/review",
            json={"action": "confirm_emergency", "reason": "Verified on camera 1"},
            cookies=cookies,
        )
        assert rev_res.status_code == 200
        assert rev_res.json()["emergency"] is True
        assert rev_res.json()["review_status"] == "confirmed"

        # 7. Update response status (New -> Dispatched)
        patch_res = client.patch(
            f"/api/incidents/{inc_id}",
            json={"response_status": "dispatched"},
            cookies=cookies,
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["response_status"] == "dispatched"

        # 8. Reset
        reset_res = client.post("/api/reset", cookies=cookies)
        assert reset_res.status_code == 200
        assert len(store.cameras) == 0
        assert len(store.incidents) == 0
    finally:
        temp_video_path.unlink(missing_ok=True)
