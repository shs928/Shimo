"""Vault 文件服务。

Markdown 文件是唯一权威数据。本服务负责：
- 目录树懒加载
- 无损读取 / 原子写入（保留 BOM 与换行风格）
- 基于内容哈希的 ETag 乐观锁
- 新建、移动（预检 + 执行）、删除（回收站）、恢复

并发策略：单进程内使用一把全局锁串行化文件变更，保证个人知识库
场景下移动/删除与保存之间不出现 TOCTOU 竞态。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from .path_guard import PathError, is_hidden_rel, normalize_rel, resolve_in_root

TRASH_DIR = ".trash"
_MAX_READ_SIZE = 16 * 1024 * 1024  # 16MB 上限的文本读取保护


class VaultError(ValueError):
    """业务层面的 Vault 操作错误。"""


class ConflictError(VaultError):
    """ETag 不匹配：文件已被其他来源修改。"""


class NotFoundError(VaultError):
    """目标文件或目录不存在。"""


class UnsupportedEncodingError(VaultError):
    """无法可靠解码为 UTF-8，只读保护。"""


def _etag_of(data: bytes) -> str:
    return f'"{hashlib.sha1(data).hexdigest()}"'


def _encode(text: str, bom: bool, newline: str) -> bytes:
    if newline == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    return data


def _decode(data: bytes) -> tuple[str, bool, str]:
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UnsupportedEncodingError("文件不是有效的 UTF-8 编码，已阻止覆盖")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, bom, newline


@dataclass
class NodeInfo:
    name: str
    path: str
    type: str  # "file" | "dir"
    size: int
    mtime_ns: int
    etag: str | None = None


@dataclass
class FileContent:
    path: str
    content: str
    etag: str
    mtime_ns: int
    size: int
    bom: bool
    newline: str


@dataclass
class MovePlan:
    src: str
    dst: str
    exists: bool
    kind: str
    valid: bool
    message: str = ""


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    # ---------- 目录树 ----------

    def ensure_initialized(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / TRASH_DIR).mkdir(parents=True, exist_ok=True)

    def list_children(self, rel: str, include_hidden: bool = False) -> list[NodeInfo]:
        if rel:
            directory = resolve_in_root(self.root, rel)
        else:
            directory = self.root
        if not directory.is_dir():
            raise NotFoundError(f"目录不存在：{rel}")

        entries: list[NodeInfo] = []
        with os.scandir(directory) as it:
            for entry in it:
                name = entry.name
                if name.startswith(".") and not include_hidden:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel_path = f"{rel}/{name}".lstrip("/") if rel else name
                if entry.is_dir(follow_symlinks=False):
                    entries.append(NodeInfo(name, rel_path, "dir", 0, st.st_mtime_ns))
                elif entry.is_file(follow_symlinks=False):
                    etag = _etag_of(b"")  # 目录树不读内容，etag 由读取接口给出
                    entries.append(NodeInfo(name, rel_path, "file", st.st_size, st.st_mtime_ns, etag))

        entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
        return entries

    # ---------- 读取 ----------

    def read_markdown(self, rel: str) -> FileContent:
        path = resolve_in_root(self.root, rel)
        if not path.is_file():
            raise NotFoundError(f"文件不存在：{rel}")
        if path.stat().st_size > _MAX_READ_SIZE:
            raise VaultError("文件过大，请在本地编辑器中打开")
        data = path.read_bytes()
        content, bom, newline = _decode(data)
        st = path.stat()
        return FileContent(
            path=rel,
            content=content,
            etag=_etag_of(data),
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
            bom=bom,
            newline=newline,
        )

    # ---------- 写入 ----------

    def write_markdown(
        self,
        rel: str,
        content: str,
        expected_etag: str | None,
        on_before_write=None,
    ) -> FileContent:
        rel = normalize_rel(rel)
        path = resolve_in_root(self.root, rel)
        with self._lock:
            if not path.exists():
                raise NotFoundError(f"文件不存在：{rel}")
            if path.is_dir():
                raise VaultError(f"目标是一个目录：{rel}")

            current = path.read_bytes()
            if expected_etag is not None and _etag_of(current) != expected_etag:
                raise ConflictError(
                    "文件已在其他设备或工具中被修改，请先刷新再决定如何处理"
                )

            text, bom, newline = _decode(current)
            if on_before_write is not None:
                on_before_write(rel, text, expected_etag)

            data = _encode(content, bom, newline)
            _atomic_write(path, data)

            return FileContent(
                path=rel,
                content=content,
                etag=_etag_of(data),
                mtime_ns=path.stat().st_mtime_ns,
                size=len(data),
                bom=bom,
                newline=newline,
            )

    # ---------- 新建 ----------

    def create(self, rel: str, kind: str = "file", initial_content: str = "") -> NodeInfo:
        rel = normalize_rel(rel)
        path = resolve_in_root(self.root, rel)
        with self._lock:
            if path.exists():
                raise VaultError(f"已存在同名文件或目录：{rel}")
            parent = path.parent
            parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                path.mkdir()
                return NodeInfo(path.name, rel, "dir", 0, path.stat().st_mtime_ns)
            if kind == "file":
                _atomic_write(path, _encode(initial_content, bom=False, newline="\n"))
                st = path.stat()
                return NodeInfo(path.name, rel, "file", st.st_size, st.st_mtime_ns)
            raise VaultError(f"未知类型：{kind}")

    # ---------- 移动 / 重命名 ----------

    def preview_move(self, src: str, dst: str) -> MovePlan:
        src = normalize_rel(src)
        dst = normalize_rel(dst)
        src_path = resolve_in_root(self.root, src)
        dst_path = resolve_in_root(self.root, dst)
        if not src_path.exists():
            return MovePlan(src, dst, False, "missing", False, "源不存在")
        kind = "dir" if src_path.is_dir() else "file"
        exists = dst_path.exists()
        message = "目标已存在" if exists else ""
        return MovePlan(src, dst, exists, kind, not exists, message)

    def move(self, src: str, dst: str) -> NodeInfo:
        plan = self.preview_move(src, dst)
        if not plan.valid:
            raise VaultError(plan.message or "无法移动")
        src_path = resolve_in_root(self.root, src)
        dst_path = resolve_in_root(self.root, dst)
        with self._lock:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src_path, dst_path)
            st = dst_path.stat()
        return NodeInfo(dst_path.name, dst, plan.kind, st.st_size if plan.kind == "file" else 0, st.st_mtime_ns)

    # ---------- 附件 ----------

    def create_unique_asset(self, name: str, data: bytes) -> str:
        """写入 assets/ 附件，重名时追加随机后缀；返回 Vault 相对路径。"""
        assets_dir = self.root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        stem, dot, ext = name.rpartition(".")
        candidate = name
        while (assets_dir / candidate).exists():
            suffix = f"-{secrets.token_hex(3)}"
            candidate = f"{stem}{suffix}.{ext}" if dot else f"{stem}{suffix}"
        _atomic_write(assets_dir / candidate, data)
        return f"assets/{candidate}"

    def import_file(self, rel: str, data: bytes) -> NodeInfo:
        """导入任意文件到指定目录（相对路径），重名时追加随机后缀；返回节点信息。"""
        rel = normalize_rel(rel)
        target = resolve_in_root(self.root, rel)
        with self._lock:
            if target.exists():
                stem, dot, ext = target.name.rpartition(".")
                suffix = f"-{secrets.token_hex(3)}"
                target = target.parent / (f"{stem}{suffix}.{ext}" if dot else f"{stem}{suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, data)
            st = target.stat()
            final_rel = target.relative_to(self.root).as_posix()
        return NodeInfo(target.name, final_rel, "file", st.st_size, st.st_mtime_ns)

    # ---------- 删除 / 回收站 ----------

    def delete(self, rel: str) -> None:
        rel = normalize_rel(rel)
        path = resolve_in_root(self.root, rel)
        with self._lock:
            if not path.exists():
                raise NotFoundError(f"目标不存在：{rel}")
            if is_hidden_rel(rel):
                raise VaultError("不允许删除隐藏目录或文件")
            trash_rel = self._unique_trash_path(rel)
            trash_path = resolve_in_root(self.root, TRASH_DIR + "/" + trash_rel)
            trash_path.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, trash_path)

    def list_trash(self) -> list[NodeInfo]:
        """列出回收站中可恢复的条目：文件 + 含文件的目录（跳过空目录）。"""
        trash_root = self.root / TRASH_DIR
        if not trash_root.exists():
            return []

        files: list[NodeInfo] = []
        for root, dirs, names in os.walk(trash_root):
            for name in names:
                full = Path(root) / name
                rel = full.relative_to(trash_root).as_posix()
                st = full.stat()
                files.append(NodeInfo(name, rel, "file", st.st_size, st.st_mtime_ns))

        dir_set: set[str] = set()
        for f in files:
            parts = Path(f.path).parts[:-1]
            for i in range(1, len(parts) + 1):
                dir_set.add("/".join(parts[:i]))

        dirs = []
        for rel in dir_set:
            full = trash_root / rel
            st = full.stat()
            name = Path(rel).name
            dirs.append(NodeInfo(name, rel, "dir", 0, st.st_mtime_ns))

        result: list[NodeInfo] = dirs + files
        result.sort(key=lambda e: (e.type != "dir", e.path.lower()))
        return result

    def restore(self, rel: str, target: str | None = None) -> NodeInfo:
        rel = normalize_rel(rel)
        src_path = resolve_in_root(self.root, TRASH_DIR + "/" + rel)
        with self._lock:
            if not src_path.exists():
                raise NotFoundError(f"回收站中不存在：{rel}")
            dst_rel = target if target else rel
            dst_path = resolve_in_root(self.root, dst_rel)
            if dst_path.exists():
                raise VaultError("目标位置已存在文件或目录，请选择恢复位置")
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            kind = "dir" if src_path.is_dir() else "file"
            os.rename(src_path, dst_path)
            st = dst_path.stat()
        return NodeInfo(dst_path.name, dst_rel, kind, st.st_size if kind == "file" else 0, st.st_mtime_ns)

    def purge_trash(self) -> int:
        """永久清空回收站，返回删除的条目数。"""
        trash_root = self.root / TRASH_DIR
        if not trash_root.exists():
            return 0
        count = 0
        with self._lock:
            for root, dirs, files in os.walk(trash_root, topdown=False):
                for name in files:
                    (Path(root) / name).unlink()
                    count += 1
                for name in dirs:
                    try:
                        (Path(root) / name).rmdir()
                    except OSError:
                        pass
        return count

    def _unique_trash_path(self, rel: str) -> str:
        """回收站内路径冲突时追加 -1/-2 后缀，保证不互相覆盖。"""
        candidate = rel
        while (self.root / TRASH_DIR / candidate).exists():
            stem, _, suffix = candidate.rpartition("/")
            base, dot, ext = (Path(candidate).stem, ".", Path(candidate).suffix) if Path(candidate).suffix else (Path(candidate).name, "", "")
            candidate = f"{stem}/{base}{dot}dup-{secrets.token_hex(3)}{ext}" if stem else f"{base}{dot}dup-{secrets.token_hex(3)}{ext}"
            candidate = candidate.replace("//", "/")
        return candidate


def _atomic_write(path: Path, data: bytes) -> None:
    """同目录临时文件写入 + flush + fsync + 原子替换。"""
    tmp = path.with_name(path.name + f".tmp-{secrets.token_hex(6)}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
