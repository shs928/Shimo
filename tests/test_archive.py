"""4.5 ZIP 导入导出：预览、防 Zip Slip、大小限制、冲突策略、导出。"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.services.archive import ZipError, export_zip, extract_zip, preview_zip


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _b(s: str) -> bytes:
    return s.encode("utf-8")


def test_preview_lists_entries(client):
    data = _make_zip({"a.md": _b("# A\n"), "sub/b.md": _b("# B\n"), "c.txt": _b("txt")})
    r = client.post("/api/v1/archive/import/preview", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200
    info = r.json()
    assert info["count"] == 3
    paths = {e["path"] for e in info["entries"]}
    assert paths == {"a.md", "sub/b.md", "c.txt"}


def test_preview_rejects_zip_slip(client):
    data = _make_zip({"../escape.md": _b("# evil\n"), "ok.md": _b("# ok\n")})
    r = client.post("/api/v1/archive/import/preview", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400
    assert "逃逸" in r.text or "非法" in r.text


def test_preview_rejects_absolute_and_hidden(client):
    data = _make_zip({"/etc/passwd": _b("x"), ".hidden.md": _b("y"), ".trash/z.md": _b("z")})
    r = client.post("/api/v1/archive/import/preview", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400


def test_preview_rejects_not_zip(client):
    r = client.post("/api/v1/archive/import/preview", files={"file": ("x.zip", _b("not a zip"), "application/zip")})
    assert r.status_code == 400
    assert "ZIP" in r.text


def test_preview_rejects_too_many_entries(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(10_001):
            zf.writestr(f"f{i}.txt", _b("x"))
    data = buf.getvalue()
    r = client.post("/api/v1/archive/import/preview", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400
    assert "条目过多" in r.text


def test_import_skip_strategy(client):
    client.post("/api/v1/files", json={"path": "a.md", "type": "file", "initial_content": "# 原有\n"})
    data = _make_zip({"a.md": _b("# 新内容\n"), "b.md": _b("# 新文件\n")})
    r = client.post("/api/v1/archive/import?strategy=skip", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200
    info = r.json()
    assert info["imported"] == 1  # b.md 导入
    assert info["skipped"] == 1  # a.md 冲突跳过
    fc = client.get("/api/v1/files/content", params={"path": "a.md"}).json()["content"]
    assert "原有" in fc  # 未被覆盖


def test_import_overwrite_saves_history(client):
    client.post("/api/v1/files", json={"path": "a.md", "type": "file", "initial_content": "# 原有内容\n"})
    data = _make_zip({"a.md": _b("# 覆盖内容\n")})
    r = client.post("/api/v1/archive/import?strategy=overwrite", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200
    assert r.json()["imported"] == 1
    # 历史快照已保存（可撤销）
    versions = client.get("/api/v1/history", params={"path": "a.md"}).json()["versions"]
    assert len(versions) == 1
    fc = client.get("/api/v1/files/content", params={"path": "a.md"}).json()["content"]
    assert "覆盖内容" in fc


def test_import_rename_strategy(client):
    client.post("/api/v1/files", json={"path": "a.md", "type": "file", "initial_content": "# 原有\n"})
    data = _make_zip({"a.md": _b("# 新内容\n")})
    r = client.post("/api/v1/archive/import?strategy=rename", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200
    assert r.json()["renamed"] == 1
    assert (client.app.state.vault.root / "a (1).md").exists()


def test_export_roundtrip(client):
    client.post("/api/v1/files", json={"path": "docs/导出.md", "type": "file", "initial_content": "# 导出\n\n独特内容 export42。\n"})
    r = client.get("/api/v1/archive/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "docs/导出.md" in names
    assert ".trash" not in names
    # 目录导出
    r2 = client.get("/api/v1/archive/export", params={"path": "docs"})
    zf2 = zipfile.ZipFile(io.BytesIO(r2.content))
    assert set(zf2.namelist()) == {"docs/导出.md"}


def test_export_excludes_trash(client):
    client.post("/api/v1/files", json={"path": "t.md", "type": "file", "initial_content": "# T\n"})
    client.app.state.vault.delete("t.md")  # 进回收站
    r = client.get("/api/v1/archive/export")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "t.md" not in zf.namelist()
    assert not any(n.startswith(".trash") for n in zf.namelist())
