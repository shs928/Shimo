"""Agent 会话存储：data/ai_agent_sessions.json。"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class AgentSessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def ls(self) -> list[dict]:
        with self._lock:
            sessions = self._load()
            items = [
                {"id": sid, "title": s.get("title") or "新会话", "updated_at": s.get("updated_at", "")}
                for sid, s in sessions.items()
            ]
            items.sort(key=lambda x: x["updated_at"], reverse=True)
            return items

    def get(self, session_id: str) -> dict | None:
        with self._lock:
            return self._load().get(session_id)

    def save(self, session_id: str, messages: list, title: str | None = None) -> dict:
        with self._lock:
            sessions = self._load()
            if session_id not in sessions:
                session_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            sessions[session_id] = {
                "id": session_id,
                "title": (title or sessions.get(session_id, {}).get("title") or "新会话"),
                "messages": messages,
                "updated_at": now,
            }
            self._save(sessions)
            return sessions[session_id]

    def remove(self, session_id: str) -> bool:
        with self._lock:
            sessions = self._load()
            if session_id not in sessions:
                return False
            del sessions[session_id]
            self._save(sessions)
            return True

    def new(self) -> dict:
        with self._lock:
            sessions = self._load()
            sid = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            sessions[sid] = {"id": sid, "title": "新会话", "messages": [], "updated_at": now}
            self._save(sessions)
            return sessions[sid]
