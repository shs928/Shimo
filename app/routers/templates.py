"""模板 API：内置/自定义模板、应用、分类、导入与导出。"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import csrf_guard, require_auth
from ..services.templates import TemplateService

router = APIRouter(prefix="/api/v1/templates", tags=["templates"], dependencies=[Depends(require_auth)])
_write_deps = [Depends(csrf_guard)]


class ApplyIn(BaseModel):
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    title: str | None = None


class CustomCreateIn(BaseModel):
    name: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = "file-text"
    content: str = ""


class CustomUpdateIn(BaseModel):
    id: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    icon: str | None = None
    content: str | None = None


class MoveIn(BaseModel):
    id: str = Field(min_length=1)
    name: str | None = None
    category: str | None = None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1)


class CategoryMoveIn(BaseModel):
    name: str = Field(min_length=1)
    new_name: str = Field(min_length=1)


def _service(request: Request) -> TemplateService:
    return request.app.state.templates


def _categories(service: TemplateService) -> dict:
    return {
        "categories": service.list_categories(),
        "custom_categories": service.list_custom_categories(),
    }


@router.get("")
def list_templates(request: Request) -> dict:
    return _service(request).list()


@router.get("/detail")
def template_detail(request: Request, id: str = Query(min_length=1)) -> dict:
    return _service(request).detail(id)


@router.post("/apply", dependencies=_write_deps)
def apply_template(payload: ApplyIn, request: Request) -> dict:
    return {"path": _service(request).apply(payload.id, payload.path, payload.title)}


@router.post("/custom", dependencies=_write_deps)
def create_custom(payload: CustomCreateIn, request: Request) -> dict:
    return _service(request).create_custom(**payload.model_dump())


@router.put("/custom", dependencies=_write_deps)
def update_custom(payload: CustomUpdateIn, request: Request) -> dict:
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    template_id = changes.pop("id")
    category = changes.pop("category", None)
    service = _service(request)
    result = service.update_custom(template_id, changes)
    if category is not None and category != result["category"]:
        result = service.move_custom(result["id"], category=category)
    return result


@router.post("/move", dependencies=_write_deps)
def move_custom(payload: MoveIn, request: Request) -> dict:
    return _service(request).move_custom(payload.id, name=payload.name, category=payload.category)


@router.post("/copy", dependencies=_write_deps)
def copy_template(payload: MoveIn, request: Request) -> dict:
    return _service(request).copy_template(payload.id, name=payload.name, category=payload.category)


@router.delete("/custom", dependencies=_write_deps)
def delete_custom(request: Request, id: str = Query(min_length=1)) -> dict:
    _service(request).delete_custom(id)
    return {"ok": True}


@router.post("/categories", dependencies=_write_deps)
def create_category(payload: CategoryIn, request: Request) -> dict:
    service = _service(request)
    service.create_category(payload.name)
    return _categories(service)


@router.post("/categories/move", dependencies=_write_deps)
def move_category(payload: CategoryMoveIn, request: Request) -> dict:
    service = _service(request)
    service.rename_category(payload.name, payload.new_name)
    return _categories(service)


@router.delete("/categories", dependencies=_write_deps)
def delete_category(
    request: Request,
    name: str = Query(min_length=1),
    force: bool = Query(default=False),
) -> dict:
    service = _service(request)
    service.delete_category(name, force=force)
    return _categories(service)


@router.post("/import", dependencies=_write_deps)
async def import_templates(
    request: Request,
    files: list[UploadFile] = File(...),
    category: str = Query(default=""),
    strategy: str = Query(default="skip", pattern="^(skip|rename|overwrite)$"),
) -> dict:
    uploaded: list[tuple[str, bytes]] = []
    for file in files:
        uploaded.append((file.filename or "template.md", await file.read()))
    return _service(request).import_markdown(uploaded, category=category, strategy=strategy)


@router.get("/export")
def export_template(request: Request, id: str = Query(min_length=1)) -> StreamingResponse:
    filename, stream = _service(request).export_one(id)
    fallback = "template.md"
    disposition = f"attachment; filename={fallback}; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        stream,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.get("/export-all")
def export_all_templates(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _service(request).export_all(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="templates.zip"'},
    )
