"""4.6 备份恢复：创建（知识/完整）、哈希校验、恢复原子替换、索引重建。"""
from __future__ import annotations

import io
import zipfile

from app.services.backup import create_backup, preview_backup, restore_backup
from app.services.archive import ZipError


def _make_vault_content(client) -> None:
    r = client.post("/api/v1/files", json={"path": "备份笔记.md", "type": "file", "initial_content": "# 备份\n\n独特备份内容 backup-random-42。\n"})
    assert r.status_code == 200, r.text
    client.post("/api/v1/files", json={"path": "sub/子文档.txt", "type": "file", "initial_content": "子文档内容"})
    client.post("/api/v1/files", json={"path": "删除.md", "type": "file", "initial_content": "# 将删除"})
    client.app.state.vault.delete("删除.md")  # 进回收站（备份应排除）


def test_create_vault_backup_excludes_trash(client):
    _make_vault_content(client)
    r = client.post("/api/v1/backup/create")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "vault/备份笔记.md" in names
    assert "vault/sub/子文档.txt" in names
    assert "删除.md" not in names
    assert not any(n.startswith("vault/.trash") for n in names)
    assert "manifest.json" in names


def test_backup_preview_and_restore_roundtrip(client):
    _make_vault_content(client)
    r = client.post("/api/v1/backup/create")
    backup_bytes = r.content

    # 预览
    p = client.post("/api/v1/backup/preview", files={"file": ("b.zip", backup_bytes, "application/zip")})
    assert p.status_code == 200
    info = p.json()
    assert info["kind"] == "vault"
    assert info["count"] >= 2

    # 篡改 vault 后恢复
    client.app.state.vault.root.joinpath("备份笔记.md").write_text("# 被篡改\n", encoding="utf-8")
    client.app.state.vault.root.joinpath("新增文件.md").write_text("# 新增\n", encoding="utf-8")

    rr = client.post("/api/v1/backup/restore", files={"file": ("b.zip", backup_bytes, "application/zip")})
    assert rr.status_code == 200, rr.text
    assert rr.json()["restored_files"] >= 2

    # 内容与哈希一致
    fc = client.get("/api/v1/files/content", params={"path": "备份笔记.md"}).json()["content"]
    assert "backup-random-42" in fc
    assert not client.app.state.vault.root.joinpath("新增文件.md").exists()
    # 索引已重建：可搜索
    hits = client.get("/api/v1/search", params={"q": "backup"}).json()["results"]
    assert any(h["path"] == "备份笔记.md" for h in hits)


def test_full_backup_restores_data_files(client):
    _make_vault_content(client)
    # 造一些历史与会话
    client.put("/api/v1/files/content?path=备份笔记.md", json={"content": "# 备份\n\n第二版内容。\n"})
    client.post("/api/v1/ai/chat/session")

    r = client.post("/api/v1/backup/create", params={"full": "true"})
    assert r.status_code == 200
    backup_bytes = r.content
    p = client.post("/api/v1/backup/preview", files={"file": ("b.zip", backup_bytes, "application/zip")})
    assert p.json()["kind"] == "full"

    # 删除历史与会话后恢复
    client.app.state.history.path.unlink()
    rr = client.post("/api/v1/backup/restore", files={"file": ("b.zip", backup_bytes, "application/zip")})
    assert rr.status_code == 200
    assert "history.json" in rr.json()["restored_data"]
    # 历史已恢复
    versions = client.get("/api/v1/history", params={"path": "备份笔记.md"}).json()["versions"]
    assert len(versions) >= 1


def test_backup_corrupt_manifest_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vault/a.md", b"# A")
    r = client.post("/api/v1/backup/preview", files={"file": ("b.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    assert "manifest" in r.text


def test_backup_hash_mismatch_rejected(client):
    _make_vault_content(client)
    r = client.post("/api/v1/backup/create")
    # 篡改备份内容但不改 manifest
    buf = io.BytesIO(r.content)
    import tempfile, os

    with tempfile.TemporaryDirectory() as td:
        zf = zipfile.ZipFile(buf)
        zf.extractall(td)
        zf.close()
        target = os.path.join(td, "vault", "备份笔记.md")
        with open(target, "w", encoding="utf-8") as f:
            f.write("# 篡改后内容")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z2:
            for root, _, files in os.walk(td):
                for name in files:
                    full = os.path.join(root, name)
                    arc = os.path.relpath(full, td).replace(os.sep, "/")
                    z2.write(full, arcname=arc)
        rr = client.post("/api/v1/backup/restore", files={"file": ("b.zip", out.getvalue(), "application/zip")})
        assert rr.status_code == 400
        assert "哈希" in rr.text
