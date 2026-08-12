"""自由对话会话存储：data/chat_sessions.json（原子写入，重启不丢）。"""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class ChatSessionStore:
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
            items = [
                {"id": sid, "title": s.get("title") or "新会话", "updated_at": s.get("updated_at", "")}
                for sid, s in self._load().items()
            ]
            items.sort(key=lambda x: x["updated_at"], reverse=True)
            return items

    def get(self, session_id: str) -> dict | None:
        with self._lock:
            return self._load().get(session_id)

    def create(self) -> dict:
        with self._lock:
            sessions = self._load()
            sid = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            sessions[sid] = {"id": sid, "title": "新会话", "messages": [], "updated_at": now}
            self._save(sessions)
            return sessions[sid]

    def append(self, session_id: str, user_msg: str, assistant_msg: str, max_history: int) -> dict:
        """追加一轮对话（用户 + 助手），保留最近 max_history 轮。"""
        with self._lock:
            sessions = self._load()
            data = sessions.get(session_id)
            if data is None:
                data = self.create()
            history = deque(data.get("messages") or [], maxlen=2 * max(1, max_history) + 2)
            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": assistant_msg})
            now = datetime.now(timezone.utc).isoformat()
            data["messages"] = list(history)
            data["updated_at"] = now
            if data.get("title") == "新会话":
                data["title"] = (user_msg or "").strip()[:30] or "新会话"
            sessions[data["id"]] = data
            self._save(sessions)
            return data

    def remove(self, session_id: str) -> bool:
        with self._lock:
            sessions = self._load()
            if session_id not in sessions:
                return False
            del sessions[session_id]
            self._save(sessions)
            return True
