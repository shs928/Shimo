"""path_guard 单元测试：路径穿越、非法名称、符号链接边界。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.path_guard import PathError, normalize_rel, resolve_in_root, validate_name


def test_normalize_rel_basics():
    assert normalize_rel("a/b.md") == "a/b.md"
    assert normalize_rel("a\\b.md") == "a/b.md"  # 反斜杠归一化
    assert normalize_rel("./a//b/") == "a/b"


def test_normalize_rel_rejects_absolute_paths():
    """绝对路径（含盘符）一律拒绝，不做去前导斜杠处理。"""
    for bad in ["/a/b", "/etc/passwd", "C:/x", "C:\\x", "c:/windows", "//server/share"]:
        with pytest.raises(PathError):
            normalize_rel(bad)


def test_normalize_rel_rejects_traversal():
    for bad in ["..", "../x", "a/../../b", "a/..", "/../x"]:
        with pytest.raises(PathError):
            normalize_rel(bad)


def test_normalize_rel_rejects_invalid_chars():
    for bad in ["a<b", "a>b", 'a:b', 'a"b', "a|b", "a?b", "a*b"]:
        with pytest.raises(PathError):
            normalize_rel(bad)
    # 反斜杠被归一化为分隔符，属合法路径
    assert normalize_rel("a\\b.md") == "a/b.md"


def test_validate_name_rejects_reserved_and_trailing_dots():
    with pytest.raises(PathError):
        validate_name("CON")
    with pytest.raises(PathError):
        validate_name("nul.txt")
    with pytest.raises(PathError):
        validate_name("trailing.")
    with pytest.raises(PathError):
        validate_name("trailing ")
    with pytest.raises(PathError):
        validate_name("")


def test_resolve_in_root_stays_inside(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    target = resolve_in_root(root, "notes/ok.md")
    assert str(target).startswith(str(root.resolve()))


def _can_symlink() -> bool:
    """探测当前进程是否有创建符号链接的权限。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        link = Path(td) / "_ln"
        target = Path(td) / "_t"
        try:
            target.touch()
            os.symlink(target, link)
            return True
        except OSError:
            return False


_SYMLINK_OK = _can_symlink()


@pytest.mark.skipif(not _SYMLINK_OK, reason="当前环境无符号链接权限")
def test_resolve_in_root_rejects_all_symlinks(tmp_path):
    """Vault 内一律拒绝符号链接，保证文件操作语义可预测。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notes").mkdir()
    os.symlink(outside, vault / "notes" / "link")

    with pytest.raises(PathError):
        resolve_in_root(vault, "notes/link/secret.txt")

    # 指向 Vault 内部的链接同样拒绝
    inner = vault / "inner.md"
    inner.write_text("x", encoding="utf-8")
    os.symlink(inner, vault / "alias.md")
    with pytest.raises(PathError):
        resolve_in_root(vault, "alias.md")
