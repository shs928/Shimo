"""版本历史：每次覆盖 Markdown 前保存旧版本快照。

- 存储：data/history.json（原子写入；与 index.db 分离，不随重建丢失）。
- SHA1 去重（内容未变不产生新版本）。
- 默认保留 50 版 / 30 天（cleanup 时清理）。
- 提供列表 / 读取 / diff / 恢复（恢复前先保存当前版本，可撤销）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_KEEP = 50
_DEFAULT_DAYS = 30


class HistoryStore:
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

    # ---------- 快照 ----------

    def save_snapshot(self, rel: str, content: str) -> None:
        """保存旧版本快照（覆盖前调用）。SHA1 去重；超限自动清理。"""
        sha1 = hashlib.sha1(content.encode("utf-8")).hexdigest()
        with self._lock:
            data = self._load()
            versions = data.setdefault(rel, [])
            if versions and versions[0]["sha1"] == sha1:
                return  # 内容未变
            versions.insert(0, {
                "sha1": sha1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "content": content,
            })
            self._trim(versions)
            data[rel] = versions
            self._save(data)

    def _trim(self, versions: list[dict]) -> None:
        """保留最近 keep 版且不超过 days 天。"""
        cutoff = time.time() - _DEFAULT_DAYS * 86400
        kept = []
        for v in versions:
            try:
                ts = datetime.fromisoformat(v["saved_at"]).timestamp()
            except (ValueError, TypeError):
                ts = time.time()
            if ts >= cutoff and len(kept) < _DEFAULT_KEEP:
                kept.append(v)
        versions[:] = kept

    # ---------- 查询 ----------

    def list_versions(self, rel: str) -> list[dict]:
        """版本元信息列表（不含正文）：[{sha1, saved_at}]。"""
        with self._lock:
            return [
                {"sha1": v["sha1"], "saved_at": v["saved_at"]}
                for v in self._load().get(rel, [])
            ]

    def get_version(self, rel: str, sha1: str) -> str | None:
        with self._lock:
            for v in self._load().get(rel, []):
                if v["sha1"] == sha1:
                    return v["content"]
        return None

    # ---------- diff ----------

    @staticmethod
    def diff(old: str, new: str) -> str:
        """简单行级 diff（统一格式，含行号上下文）。"""
        import difflib

        return "\n".join(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="旧版本", tofile="当前",
            lineterm="",
        ))

    # ---------- 恢复 ----------

    def restore(self, rel: str, sha1: str, vault, indexer, rag, mark_self_write=None) -> str:
        """恢复指定版本：先保存当前版本快照，再写回文件并重建索引。

        返回恢复后的内容。
        """
        content = self.get_version(rel, sha1)
        if content is None:
            raise ValueError("版本不存在")
        current = vault.read_markdown(rel).content
        self.save_snapshot(rel, current)  # 恢复可撤销
        result = vault.write_markdown(rel, content, None)
        if mark_self_write:
            mark_self_write(rel)
        indexer.index_file(rel)
        rag.reindex_file(rel, result.content)
        return result.content
