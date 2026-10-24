import json
import os
import secrets
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
EVIDENCE_DIR = DATA_DIR / "evidence"
OUTBOX_DIR = DATA_DIR / "outbox"
for _p in (UPLOAD_DIR, EVIDENCE_DIR, OUTBOX_DIR):
    _p.mkdir(parents=True, exist_ok=True)

SEGMENT_SEC = float(os.getenv("SEGMENT_SEC", "2"))          # video seconds per analysis segment
SAMPLE_FPS = float(os.getenv("SAMPLE_FPS", "5"))            # frames analysed per video second
ANALYSIS_SPEED = float(os.getenv("ANALYSIS_SPEED", "2"))    # 1 = real time, 2 = twice real time
EVENT_COOLDOWN_SEC = float(os.getenv("EVENT_COOLDOWN_SEC", "10"))
MAX_CAMERAS = int(os.getenv("MAX_CAMERAS", "81"))
YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
YOLO_CONF = float(os.getenv("YOLO_CONF", "0.35"))
MONGO_URL = os.getenv("MONGO_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")


def load_zones() -> dict:
    try:
        raw = json.loads((BASE / "zones.json").read_text())
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}

EVIDENCE_PRE_SEC = int(os.getenv("EVIDENCE_PRE_SEC", "3"))     # frames saved before the event (1 per second)
EVIDENCE_POST_SEC = int(os.getenv("EVIDENCE_POST_SEC", "4"))   # frames saved after the event (1 per second)
CONGESTION_MIN = int(os.getenv("CONGESTION_MIN", "8"))         # slow vehicles needed to flag congestion

# Access control. Format: "name:password,name2:password2". Change these before any real use.
USERS = {}
for _pair in os.getenv("USERS", "admin:admin123,reviewer:review123").split(","):
    _u, _, _p = _pair.partition(":")
    if _u.strip():
        USERS[_u.strip()] = _p.strip()
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)  # unset = sessions reset on restart
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# Email alerts. Without SMTP_HOST the message is written to data/outbox/ instead of being sent.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "incident-desk@localhost")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "admin@example.com")
EMAIL_MIN_SEVERITY = os.getenv("EMAIL_MIN_SEVERITY", "high")
