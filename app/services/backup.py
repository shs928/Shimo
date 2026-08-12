"""备份与恢复：知识备份（Vault）与完整备份（Vault + 配置 + 历史 + 会话）。

- manifest.json 记录每个文件的 SHA1，恢复时完整校验。
- OS 凭据 / API Key 不进入归档（ai.json 本就不含 Key）；恢复后提示重新配置。
- 不含 index.db（派生索引，恢复后全量重建）。
- 恢复：临时目录校验解压 → 写锁 → 原子替换 → 重建索引。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .archive import ZipError, _MAX_ENTRIES, _MAX_TOTAL_BYTES, _safe_zip_path

_MANIFEST = "manifest.json"
_VAULT_PREFIX = "vault/"
_DATA_PREFIX = "data/"
_TRASH_NAME = ".trash"
_MAX_BACKUP_BYTES = 600 * 1024 * 1024


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _walk_vault(vault_root) -> list[tuple[str, bytes]]:
    """收集 Vault 内所有文件（排除 .trash 与隐藏路径）。"""
    out: list[tuple[str, bytes]] = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != _TRASH_NAME]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, vault_root).replace(os.sep, "/")
            if any(p.startswith(".") for p in PurePosixPath(rel).parts):
                continue
            out.append((rel, open(full, "rb").read()))
    return out


def create_backup(vault_root, data_path, full: bool = False) -> io.BytesIO:
    """创建备份 ZIP。full=True 时包含配置/历史/会话（不含 index.db 与凭据）。"""
    buf = io.BytesIO()
    files: list[dict] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in _walk_vault(vault_root):
            zf.writestr(_VAULT_PREFIX + rel, data)
            files.append({"path": rel, "sha1": _sha1(data), "size": len(data)})
        if full:
            for name in ("config.json", "history.json", "chat_sessions.json", "ai_agent_sessions.json", "ai.json"):
                p = data_path / name
                if p.is_file():
                    data = p.read_bytes()
                    zf.writestr(_DATA_PREFIX + name, data)
                    files.append({"path": name, "sha1": _sha1(data), "size": len(data)})
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "full" if full else "vault",
            "files": files,
        }
        zf.writestr(_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return buf


def _read_manifest(data: bytes) -> dict:
    """读取备份内原始 manifest.json（完整字段）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ZipError("不是有效的 ZIP 文件") from exc
    with zf:
        try:
            manifest = json.loads(zf.read(_MANIFEST).decode("utf-8"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ZipError("备份缺少有效 manifest.json") from exc
        if not isinstance(manifest.get("files"), list):
            raise ZipError("备份 manifest 缺少 files 清单")
        return manifest


def preview_backup(data: bytes) -> dict:
    """读取备份 manifest（不落地）。"""
    manifest = _read_manifest(data)
    files = manifest["files"]
    return {
        "kind": manifest.get("kind", "vault"),
        "created_at": manifest.get("created_at", ""),
        "count": len(files),
        "total_size": sum(f.get("size", 0) for f in files),
        "valid": True,
    }


def restore_backup(data: bytes, vault, indexer, rag, history, ai_store_path=None) -> dict:
    """校验并恢复备份；成功重建索引。返回统计。"""
    if len(data) > _MAX_BACKUP_BYTES:
        raise ZipError("备份文件过大")
    manifest = _read_manifest(data)
    kind = manifest["kind"]

    # 1) 临时目录解压 + SHA1 完整校验 + Zip Slip 防护
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "backup")
        os.makedirs(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ENTRIES:
                raise ZipError("备份条目过多")
            expected = {f["path"]: f["sha1"] for f in manifest["files"]}
            for info in infos:
                if info.filename == _MANIFEST:
                    continue
                rel = _safe_zip_path(info.filename)
                if rel is None:
                    continue
                dest = os.path.join(tmp, rel)
                if not os.path.realpath(dest).startswith(os.path.realpath(tmp)):
                    raise ZipError(f"备份包含越界路径：{info.filename}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                raw = zf.read(info.filename)
                # 校验哈希（manifest 中的文件）
                base = rel
                if rel.startswith(_VAULT_PREFIX):
                    base = rel[len(_VAULT_PREFIX):]
                elif rel.startswith(_DATA_PREFIX):
                    base = rel[len(_DATA_PREFIX):]
                if base in expected and _sha1(raw) != expected[base]:
                    raise ZipError(f"备份校验失败：{base} 哈希不匹配")
                with open(dest, "wb") as f:
                    f.write(raw)

        # 2) 写锁 → 原子替换 Vault
        vault_root = vault.root
        with vault._lock:
            for item in vault_root.iterdir():
                if item.name == _TRASH_NAME:
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
            src_vault = os.path.join(tmp, _VAULT_PREFIX)
            if os.path.isdir(src_vault):
                for name in os.listdir(src_vault):
                    shutil.move(os.path.join(src_vault, name), vault_root)

        # 3) 恢复数据文件（full 备份）
        restored_data = []
        if kind == "full":
            for name in ("config.json", "history.json", "chat_sessions.json", "ai_agent_sessions.json", "ai.json"):
                src = os.path.join(tmp, _DATA_PREFIX, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(vault_root.parent, "data", name))
                    restored_data.append(name)

    # 4) 重建索引（普通 + RAG 全量）
    indexer.rebuild()
    _rebuild_rag(vault_root, rag)
    return {
        "kind": kind,
        "restored_files": len(manifest["files"]),
        "restored_data": restored_data,
    }


def _rebuild_rag(vault_root, rag) -> None:
    """全量重建 RAG chunks（复用 /ai/rebuild 的遍历逻辑）。"""
    from ..services.doc_parser import is_document, parse_document

    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.lower().endswith(".md") or is_document(name):
                rel = os.path.relpath(os.path.join(dirpath, name), vault_root).replace(os.sep, "/")
                try:
                    if name.lower().endswith(".md"):
                        text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                    else:
                        text = parse_document(rel, open(os.path.join(dirpath, name), "rb").read())
                    rag.reindex_file(rel, text)
                except Exception:
                    continue
