"""Vault 文件服务单元测试：原子写入、ETag、移动、回收站。"""
from __future__ import annotations

import pytest

from app.services.vault import (
    ConflictError,
    NotFoundError,
    Vault,
    VaultError,
)


def test_create_and_read(vault: Vault):
    vault.create("notes/hello.md", "file", "# Hello\n")
    fc = vault.read_markdown("notes/hello.md")
    assert fc.content == "# Hello\n"
    assert fc.etag.startswith('"')
    assert fc.bom is False
    assert fc.newline == "\n"


def test_read_missing_raises(vault: Vault):
    with pytest.raises(NotFoundError):
        vault.read_markdown("nope.md")


def test_write_with_etag(vault: Vault):
    vault.create("a.md", "file", "v1")
    fc = vault.read_markdown("a.md")

    updated = vault.write_markdown("a.md", "v2", expected_etag=fc.etag)
    assert updated.etag != fc.etag
    assert vault.read_markdown("a.md").content == "v2"


def test_write_stale_etag_conflicts(vault: Vault):
    vault.create("a.md", "file", "v1")
    fc = vault.read_markdown("a.md")
    # 模拟外部修改（绕过 ETag 校验直接写入）
    vault.write_markdown("a.md", "external", expected_etag=None)
    with pytest.raises(ConflictError):
        vault.write_markdown("a.md", "my change", expected_etag=fc.etag)


def test_write_preserves_bom_and_crlf(vault: Vault):
    data = b"\xef\xbb\xbfline1\r\nline2\r\n"
    (vault.root / "crlf.md").write_bytes(data)
    fc = vault.read_markdown("crlf.md")
    assert fc.bom is True
    assert fc.newline == "\r\n"
    assert fc.content == "line1\r\nline2\r\n"

    vault.write_markdown("crlf.md", "a\r\nb\r\n", expected_etag=fc.etag)
    raw = (vault.root / "crlf.md").read_bytes()
    assert raw == b"\xef\xbb\xbfa\r\nb\r\n"


def test_write_rejects_binary(vault: Vault):
    (vault.root / "bin.md").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(VaultError):
        vault.read_markdown("bin.md")


def test_create_dir_and_tree(vault: Vault):
    vault.create("技术/AI", "dir")
    vault.create("技术/AI/rag.md", "file", "# RAG")
    vault.create("随笔.md", "file", "# 随笔")

    entries = vault.list_children("")
    names = [(e.name, e.type) for e in entries]
    assert ("技术", "dir") in names
    assert ("随笔.md", "file") in names

    sub = vault.list_children("技术/AI")
    assert sub[0].name == "rag.md"
    assert sub[0].type == "file"


def test_duplicate_create_rejected(vault: Vault):
    vault.create("a.md", "file")
    with pytest.raises(VaultError):
        vault.create("a.md", "file")


def test_move(vault: Vault):
    vault.create("old.md", "file", "content")
    vault.move("old.md", "new/place.md")
    assert (vault.root / "new" / "place.md").is_file()
    assert not (vault.root / "old.md").exists()


def test_move_over_existing_rejected(vault: Vault):
    vault.create("a.md", "file", "a")
    vault.create("b.md", "file", "b")
    plan = vault.preview_move("a.md", "b.md")
    assert plan.valid is False
    assert plan.exists is True
    with pytest.raises(VaultError):
        vault.move("a.md", "b.md")


def test_delete_and_restore(vault: Vault):
    vault.create("notes/gone.md", "file", "x")
    vault.delete("notes/gone.md")
    assert not (vault.root / "notes" / "gone.md").exists()

    trash = vault.list_trash()
    assert any(e.path == "notes/gone.md" and e.type == "file" for e in trash)

    restored = vault.restore("notes/gone.md")
    assert restored.path == "notes/gone.md"
    assert (vault.root / "notes" / "gone.md").is_file()


def test_delete_twice_keeps_both_in_trash(vault: Vault):
    vault.create("x.md", "file", "1")
    vault.delete("x.md")
    vault.create("x.md", "file", "2")
    vault.delete("x.md")
    trash = vault.list_trash()
    assert len(trash) == 2


def test_purge_trash(vault: Vault):
    vault.create("x.md", "file", "1")
    vault.create("y.md", "file", "2")
    vault.delete("x.md")
    vault.delete("y.md")
    assert vault.purge_trash() == 2
    assert vault.list_trash() == []


def test_hidden_entries_excluded_from_tree(vault: Vault):
    (vault.root / ".private").mkdir()
    (vault.root / ".private" / "s.md").write_text("s", encoding="utf-8")
    names = [e.name for e in vault.list_children("")]
    assert ".private" not in names
    assert vault.list_children("", include_hidden=True)
