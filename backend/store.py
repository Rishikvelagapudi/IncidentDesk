"""In-memory store with JSON persistence and an optional MongoDB mirror (set MONGO_URL)."""
import json
import logging
import shutil
import threading
from datetime import datetime

from config import DATA_DIR, EVIDENCE_DIR, MONGO_URL

log = logging.getLogger("store")


class Store:
    def __init__(self):
        self.cameras: dict[str, dict] = {}
        self.paths: dict[str, str] = {}
        self.incidents: dict[str, dict] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._file = DATA_DIR / "incidents.json"
        self._col = None
        if MONGO_URL:
            try:
                from pymongo import MongoClient

                self._col = MongoClient(MONGO_URL, serverSelectionTimeoutMS=2000)["incident_desk"]["incidents"]
            except Exception as exc:  # noqa: BLE001
                log.warning("MongoDB mirror disabled: %s", exc)

    def next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"INC-{datetime.now().year}-{self._counter:03d}"

    def add_incident(self, inc: dict):
        self.incidents[inc["id"]] = inc
        self.save_incident(inc)

    def save_incident(self, inc: dict):
        with self._lock:
            self._file.write_text(json.dumps(list(self.incidents.values()), indent=2))
        if self._col is not None:
            try:
                self._col.replace_one({"id": inc["id"]}, inc, upsert=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("MongoDB write failed: %s", exc)

    def incidents_for(self, cam_id: str) -> list[dict]:
        return [i for i in self.incidents.values() if i["camera_id"] == cam_id]

    def clear(self):
        self.cameras.clear()
        self.paths.clear()
        self.incidents.clear()
        self._counter = 0
        self._file.write_text("[]")
        for d in EVIDENCE_DIR.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            else:
                d.unlink(missing_ok=True)
