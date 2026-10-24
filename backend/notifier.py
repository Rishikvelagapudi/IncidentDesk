"""Email alert: summary plus a link to the authenticated workbench. Never attaches footage."""
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from config import ALERT_EMAIL_TO, OUTBOX_DIR, PUBLIC_URL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

PRIORITY = {"critical": "HIGH PRIORITY", "high": "HIGH PRIORITY", "medium": "MEDIUM PRIORITY", "low": "LOW PRIORITY"}


def build(inc: dict) -> EmailMessage:
    link = f"{PUBLIC_URL}/incidents/{inc['id']}"
    msg = EmailMessage()
    msg["Subject"] = f"[{PRIORITY[inc['severity']]}] Potential {inc['short']} — {inc['camera_id']}"
    msg["From"], msg["To"] = SMTP_FROM, ALERT_EMAIL_TO
    msg.set_content(
        f"Incident ID: {inc['id']}\n"
        f"Incident category: {inc['category']} / {inc['label']}\n"
        f"Detection timestamp: video {inc['video_ts_text']} (detected {inc['detected_at']})\n"
        f"Camera location: {inc['camera_name']} ({inc['camera_id']})\n"
        f"Preliminary severity (AI assessment only): {inc['severity']}\n"
        f"Review status: {inc['review_status'].replace('_', ' ')}\n\n"
        f"Summary: {inc['summary']}\n\n"
        f"Open the incident report (sign-in required): {link}\n\n"
        "This alert is an AI detection awaiting human review. It is not a confirmed emergency."
    )
    return msg


def send(inc: dict) -> dict:
    msg = build(inc)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not SMTP_HOST:
        (OUTBOX_DIR / f"{inc['id']}.eml").write_bytes(bytes(msg))
        return dict(status="logged", to=ALERT_EMAIL_TO, at=at, detail="SMTP not configured; saved to data/outbox")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        return dict(status="sent", to=ALERT_EMAIL_TO, at=at, detail="")
    except Exception as exc:  # noqa: BLE001
        return dict(status="failed", to=ALERT_EMAIL_TO, at=at, detail=str(exc))
