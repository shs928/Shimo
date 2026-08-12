"""文件元信息 API：大纲、front matter、显示标题。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import get_vault, require_auth
from ..services.metadata import doc_title, parse_frontmatter, parse_headings
from ..services.path_guard import PathError
from ..services.vault import Vault, VaultError

router = APIRouter(prefix="/api/v1", tags=["metadata"], dependencies=[Depends(require_auth)])


def _read_text(vault: Vault, path: str) -> str:
    if not path.endswith(".md"):
        raise VaultError("仅支持 Markdown 文件")
    return vault.read_markdown(path).content


@router.get("/files/meta")
def file_meta(request: Request, vault: Vault = Depends(get_vault), path: str = Query(min_length=1)) -> dict:
    fc = vault.read_markdown(path)
    text = fc.content
    fm = parse_frontmatter(text)
    return {
        "path": path,
        "title": doc_title(text, fc.path.rsplit("/", 1)[-1].removesuffix(".md")),
        "frontmatter": fm.data,
        "has_frontmatter": bool(fm.raw),
        "size": fc.size,
        "mtime_ns": fc.mtime_ns,
        "etag": fc.etag,
    }


@router.get("/files/outline")
def outline(request: Request, vault: Vault = Depends(get_vault), path: str = Query(min_length=1)) -> dict:
    text = _read_text(vault, path)
    headings = parse_headings(text)
    return {"headings": [h.__dict__ for h in headings]}
