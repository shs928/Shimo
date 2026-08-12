"""ZIP 导入导出：预览、安全解压、冲突策略、导出。

安全：
- 防 Zip Slip：条目路径规范化后必须为相对路径且不逃逸目标目录。
- 条目数 ≤ 10000；总展开大小 ≤ 500MB；单文件 ≤ 50MB。
- 写入前校验目标路径走 path_guard（与 Vault 全局规则一致）。

冲突策略：skip（跳过）/ rename（自动加后缀）/ overwrite（先历史快照）。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

from ..services.path_guard import PathError, normalize_rel
from ..services.vault import VaultError

_MAX_ENTRIES = 10_000
_MAX_TOTAL_BYTES = 500 * 1024 * 1024
_MAX_FILE_BYTES = 50 * 1024 * 1024
_TRASH_NAME = ".trash"


class ZipError(VaultError):
    pass


def _safe_zip_path(name: str) -> str | None:
    """校验 zip 条目路径：相对、无 ..、无盘符、非目录分隔符攻击。返回规范化路径。"""
    if not name or name.endswith("/"):
        return None  # 目录条目跳过
    if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
        raise ZipError(f"压缩包内存在非法绝对路径：{name}")
    try:
        rel = normalize_rel(name)
    except PathError as exc:
        raise ZipError(f"压缩包内存在非法路径 {name!r}：{exc}") from exc
    if rel.startswith(_TRASH_NAME) or any(p.startswith(".") for p in PurePosixPath(rel).parts):
        raise ZipError(f"压缩包内不允许隐藏路径/回收站：{name}")
    return rel


def preview_zip(data: bytes) -> dict:
    """预览压缩包内容（不解压）：条目清单、冲突检测、大小限制。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ZipError("不是有效的 ZIP 文件") from exc

    with zf:
        entries = zf.infolist()
        if len(entries) > _MAX_ENTRIES:
            raise ZipError(f"压缩包条目过多（超过 {_MAX_ENTRIES}）")
        total = 0
        out = []
        for info in entries:
            rel = _safe_zip_path(info.filename)
            if rel is None:
                continue
            if info.file_size > _MAX_FILE_BYTES:
                raise ZipError(f"单文件超过 50MB 上限：{rel}")
            total += info.file_size
            if total > _MAX_TOTAL_BYTES:
                raise ZipError("压缩包总展开大小超过 500MB 上限")
            out.append({"path": rel, "size": info.file_size})
    return {"entries": out, "count": len(out), "total_size": total}


def extract_zip(data: bytes, strategy: str, vault, history, indexer, rag) -> dict:
    """按策略解压导入。strategy ∈ skip | rename | overwrite。"""
    if strategy not in ("skip", "rename", "overwrite"):
        raise ZipError(f"未知冲突策略：{strategy}")
    preview = preview_zip(data)
    imported = 0
    skipped = 0
    renamed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for entry in preview["entries"]:
            rel = entry["path"]
            target = vault.root / rel
            exists = target.exists()
            if exists:
                if strategy == "skip":
                    skipped += 1
                    continue
                if strategy == "rename":
                    rel = _unique_name(vault, rel)
                else:  # overwrite：先历史快照，再直接覆盖（import_file 对冲突会自动改名）
                    if rel.lower().endswith(".md"):
                        try:
                            history.save_snapshot(rel, vault.read_markdown(rel).content)
                        except Exception:
                            pass
                    _atomic_write_file(target, zf.read(_find_entry_name(zf, entry["path"])))
                    _index_imported(vault, indexer, rag, rel)
                    imported += 1
                    continue
            raw = zf.read(_find_entry_name(zf, entry["path"]))
            node = vault.import_file(rel, raw)
            _index_imported(vault, indexer, rag, node.path)
            imported += 1
            if rel != entry["path"]:
                renamed += 1
    return {"imported": imported, "skipped": skipped, "renamed": renamed, "count": preview["count"]}


def _atomic_write_file(target, data: bytes) -> None:
    """原子覆盖写入（临时文件 + replace），供 overwrite 策略使用。"""
    import os
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".zip-import-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find_entry_name(zf: zipfile.ZipFile, rel: str) -> str:
    """按规范化路径匹配 zip 内原始条目名。"""
    for info in zf.infolist():
        try:
            if _safe_zip_path(info.filename) == rel:
                return info.filename
        except ZipError:
            continue
    raise ZipError(f"压缩包条目丢失：{rel}")


def _unique_name(vault, rel: str) -> str:
    """重名时追加 (1)/(2)… 后缀（与 vault.import_file 行为一致）。"""
    parts = rel.rsplit(".", 1)
    base, ext = (parts[0], "." + parts[1]) if len(parts) == 2 else (rel, "")
    i = 1
    while (vault.root / f"{base} ({i}){ext}").exists():
        i += 1
    return f"{base} ({i}){ext}"


def _index_imported(vault, indexer, rag, rel: str) -> None:
    from ..services.doc_parser import is_document, parse_document

    try:
        if rel.lower().endswith(".md"):
            fc = vault.read_markdown(rel)
            indexer.index_file(rel)
            rag.reindex_file(rel, fc.content)
        elif is_document(rel):
            text = parse_document(rel, (vault.root / rel).read_bytes())
            if text:
                rag.reindex_file(rel, text)
    except Exception:
        pass


def export_zip(vault, rel_dir: str = "") -> io.BytesIO:
    """导出目录（空 = 全库）为 ZIP；排除 .trash 与隐藏路径。"""
    buf = io.BytesIO()
    root = vault.root
    base = normalize_rel(rel_dir) if rel_dir else ""
    base_path = root if not base else root / base
    if not base_path.is_dir():
        raise VaultError(f"目录不存在：{base or '/'}")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in __import__("os").walk(base_path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != _TRASH_NAME]
            for name in filenames:
                full = __import__("pathlib").Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                if any(p.startswith(".") for p in PurePosixPath(rel).parts):
                    continue
                zf.write(full, arcname=rel)
    buf.seek(0)
    return buf
