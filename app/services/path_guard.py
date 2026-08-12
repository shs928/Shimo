"""Vault 路径安全边界。

所有进入 Vault 的路径都必须经过本模块：
- 拒绝绝对路径、`..` 穿越、空字节和 Windows 非法字符。
- 拒绝 Windows 保留名（CON、NUL 等）与易出问题的尾随点/空格。
- 解析后确认目标仍位于 Vault 根目录内。
- 一律拒绝符号链接（不区分指向内外），保证文件操作语义可预测。
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

class PathError(ValueError):
    """非法的 Vault 相对路径。"""


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = set("\x00<>:\"|?*\\")
_MAX_DEPTH = 64
_MAX_NAME_LEN = 200


def normalize_rel(rel: str) -> str:
    """校验并规范化一个 Vault 相对路径（POSIX 风格，正斜杠分隔）。

    返回规范化后的相对路径；非法时抛 PathError。
    """
    if not isinstance(rel, str) or not rel.strip() or rel.strip() == "/":
        raise PathError("路径不能为空")

    p = PurePosixPath(rel.replace("\\", "/"))
    parts = [part for part in p.parts if part not in ("", ".", "/")]

    if any(part == ".." for part in parts):
        raise PathError("路径不允许包含 ..")

    if not parts:
        raise PathError("路径不能为空")

    if len(parts) > _MAX_DEPTH:
        raise PathError("路径层级过深")

    for part in parts:
        _validate_name(part)

    return "/".join(parts)


def validate_name(name: str) -> str:
    """校验单个文件名 / 目录名，返回去除首尾空白后的名称。"""
    if not isinstance(name, str) or not name.strip():
        raise PathError("名称不能为空")
    if name.endswith((".", " ")):
        raise PathError("名称不能以点或空格结尾")
    name = name.strip()
    if "/" in name or "\\" in name:
        raise PathError("名称不能包含路径分隔符")
    _validate_name(name)
    return name


def _validate_name(name: str) -> None:
    if len(name) > _MAX_NAME_LEN:
        raise PathError("名称过长")
    if any(ch in _INVALID_CHARS for ch in name):
        raise PathError("名称包含非法字符：< > : \" | ? * \\ 或控制字符")
    if name.endswith((".", " ")):
        raise PathError("名称不能以点或空格结尾")
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise PathError(f"保留名称不可用：{stem}")


def resolve_in_root(root: Path, rel: str) -> Path:
    """将相对路径解析为绝对路径，并确保不逃逸 Vault 根目录。

    额外拒绝符号链接目标指向 Vault 外部的情况。
    """
    rel = normalize_rel(rel)
    root_resolved = root.resolve()
    candidate = root_resolved / rel

    # 已存在的符号链接可能指向外部：拒绝跟随
    if candidate.is_symlink():
        raise PathError("不允许通过符号链接访问 Vault 外部")

    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        raise PathError("路径无法解析")

    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PathError("路径超出 Vault 边界")

    return candidate


def is_hidden_rel(rel: str) -> bool:
    """判断路径任一分段是否以点开头（隐藏文件 / 目录）。"""
    return any(part.startswith(".") for part in PurePosixPath(rel).parts)
