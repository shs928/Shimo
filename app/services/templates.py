"""Markdown 模板服务：内置模板、自定义模板、变量应用与导入导出。"""
from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .metadata import doc_title, parse_frontmatter
from .path_guard import (
    PathError,
    is_hidden_rel,
    is_templates_rel,
    normalize_rel,
    resolve_in_root,
    validate_name,
)
from .vault import NotFoundError, VaultError

TEMPLATES_DIR = "templates"
_TEMPLATE_KEYS = {
    "template_title",
    "template_description",
    "template_category",
    "template_tags",
    "template_icon",
}
_MAX_IMPORT_FILE = 4 * 1024 * 1024
_MAX_IMPORT_TOTAL = 20 * 1024 * 1024
_PLACEHOLDER_RE = re.compile(r"\{\{(title|date|time|datetime)\}\}")


BUILTIN_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "slug": "meeting-notes",
        "title": "会议纪要",
        "description": "记录会议议题、结论、行动项与负责人。",
        "category": "工作",
        "tags": ["会议", "工作"],
        "icon": "users",
        "content": """# {{title}}\n\n- 日期：{{date}}\n- 时间：{{time}}\n- 参会人：\n- 主持人：\n\n## 会议议题\n\n1. \n\n## 讨论与结论\n\n\n## 行动项\n\n- [ ] 任务 — 负责人： — 截止日期：\n""",
    },
    {
        "slug": "weekly-report",
        "title": "工作周报",
        "description": "汇总本周成果、问题、下周计划与所需支持。",
        "category": "工作",
        "tags": ["周报", "工作"],
        "icon": "calendar-days",
        "content": """# {{title}}\n\n> {{date}}\n\n## 本周完成\n\n- \n\n## 进展与成果\n\n\n## 问题与风险\n\n- \n\n## 下周计划\n\n- [ ] \n\n## 需要支持\n\n- \n""",
    },
    {
        "slug": "project-proposal",
        "title": "项目立项",
        "description": "定义项目背景、目标、范围、里程碑和风险。",
        "category": "项目",
        "tags": ["项目", "立项"],
        "icon": "briefcase-business",
        "content": """# {{title}}\n\n- 创建时间：{{datetime}}\n- 项目负责人：\n- 项目周期：\n\n## 背景与价值\n\n\n## 项目目标\n\n\n## 范围\n\n### 包含\n\n- \n\n### 不包含\n\n- \n\n## 里程碑\n\n| 里程碑 | 计划日期 | 交付物 |\n| --- | --- | --- |\n|  |  |  |\n\n## 风险与应对\n\n| 风险 | 影响 | 应对措施 |\n| --- | --- | --- |\n|  |  |  |\n""",
    },
    {
        "slug": "requirements",
        "title": "需求文档",
        "description": "描述用户问题、使用场景、需求范围与验收标准。",
        "category": "项目",
        "tags": ["需求", "产品"],
        "icon": "notebook-tabs",
        "content": """# {{title}}\n\n- 日期：{{date}}\n- 负责人：\n- 状态：草稿\n\n## 背景\n\n\n## 用户与场景\n\n\n## 需求说明\n\n### 功能需求\n\n1. \n\n### 非功能需求\n\n- \n\n## 验收标准\n\n- [ ] \n\n## 非目标\n\n- \n""",
    },
    {
        "slug": "task-list",
        "title": "任务清单",
        "description": "按优先级组织待办事项、截止日期与备注。",
        "category": "工作",
        "tags": ["任务", "待办"],
        "icon": "list-checks",
        "content": """# {{title}}\n\n> 更新于 {{datetime}}\n\n## 今天\n\n- [ ] \n\n## 本周\n\n- [ ] \n\n## 稍后\n\n- [ ] \n\n## 已完成\n\n- [x] \n""",
    },
    {
        "slug": "retrospective",
        "title": "复盘记录",
        "description": "回顾目标与结果，提炼经验、问题和改进行动。",
        "category": "工作",
        "tags": ["复盘", "总结"],
        "icon": "rotate-ccw",
        "content": """# {{title}}\n\n- 复盘日期：{{date}}\n- 参与人：\n\n## 原定目标\n\n\n## 实际结果\n\n\n## 做得好的\n\n- \n\n## 可以改进的\n\n- \n\n## 原因分析\n\n\n## 后续行动\n\n- [ ] 行动 — 负责人： — 截止日期：\n""",
    },
    {
        "slug": "reading-notes",
        "title": "读书笔记",
        "description": "整理书籍信息、核心观点、摘录和个人启发。",
        "category": "学习",
        "tags": ["阅读", "笔记"],
        "icon": "book-open",
        "content": """# {{title}}\n\n- 书名：\n- 作者：\n- 阅读日期：{{date}}\n- 评分：\n\n## 内容概述\n\n\n## 核心观点\n\n1. \n\n## 摘录\n\n> \n\n## 我的思考\n\n\n## 可执行启发\n\n- [ ] \n""",
    },
    {
        "slug": "research-notes",
        "title": "研究记录",
        "description": "记录研究问题、资料、过程、证据与阶段结论。",
        "category": "研究",
        "tags": ["研究", "记录"],
        "icon": "microscope",
        "content": """# {{title}}\n\n- 记录时间：{{datetime}}\n- 研究主题：\n\n## 研究问题\n\n\n## 假设\n\n\n## 资料与来源\n\n- \n\n## 研究过程\n\n\n## 发现与证据\n\n\n## 阶段结论\n\n\n## 待验证事项\n\n- [ ] \n""",
    },
)

_BUILTIN_BY_ID = {f"builtin:{item['slug']}": item for item in BUILTIN_TEMPLATES}


class TemplateService:
    """统一管理内置模板与 ``vault/templates`` 下的自定义 Markdown。"""

    def __init__(self, vault, history=None, indexer=None, rag=None, watcher=None, event_hub=None):
        self.vault = vault
        self.history = history
        self.indexer = indexer
        self.rag = rag
        self.watcher = watcher
        self.event_hub = event_hub
        self.templates_root = resolve_in_root(vault.root, TEMPLATES_DIR)
        self.templates_root.mkdir(parents=True, exist_ok=True)
        # 即使 schema 无需全量重建，也清掉旧版本可能留下的模板派生索引。
        self._clear_indexes(TEMPLATES_DIR)

    # ---------- 查询 ----------

    def list(self) -> dict:
        templates = [self._builtin_detail(item, include_content=False) for item in BUILTIN_TEMPLATES]
        templates.extend(self._custom_detail(path, include_content=False) for path in self._iter_custom_paths())
        templates.sort(key=lambda item: (item["source"] != "builtin", item["category"], item["title"].casefold()))
        return {
            "templates": templates,
            "categories": self.list_categories(),
            "custom_categories": self.list_custom_categories(),
        }

    def detail(self, template_id: str) -> dict:
        builtin = _BUILTIN_BY_ID.get(template_id)
        if builtin is not None:
            return self._builtin_detail(builtin, include_content=True)
        path = self._custom_path_from_id(template_id, must_exist=True)
        return self._custom_detail(path, include_content=True)

    def list_categories(self) -> list[str]:
        categories = {item["category"] for item in BUILTIN_TEMPLATES if item["category"]}
        categories.update(self.list_custom_categories())
        return sorted(categories, key=str.casefold)

    def list_custom_categories(self) -> list[str]:
        categories: set[str] = set()
        for dirpath, dirnames, _filenames in os.walk(self.templates_root, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and not (Path(dirpath) / name).is_symlink()
            ]
            path = Path(dirpath)
            if path != self.templates_root:
                categories.add(path.relative_to(self.templates_root).as_posix())
        return sorted(categories, key=str.casefold)

    # ---------- CRUD ----------

    def create_custom(
        self,
        *,
        name: str,
        title: str = "",
        description: str = "",
        category: str = "",
        tags: list[str] | None = None,
        icon: str = "file-text",
        content: str = "",
    ) -> dict:
        category = self._normalize_category(category)
        filename = self._markdown_name(name)
        rel = self._custom_rel(category, filename)
        target = resolve_in_root(self.vault.root, rel)
        if target.exists():
            raise VaultError(f"模板已存在：{rel}")

        inferred = self._infer_metadata(content, Path(filename).stem)
        meta = {
            "title": self._clean_text(title) or inferred["title"],
            "description": self._clean_text(description) or inferred["description"],
            "category": category,
            "tags": self._clean_tags(tags if tags is not None else inferred["tags"]),
            "icon": self._clean_text(icon) or inferred["icon"],
        }
        raw = self._with_template_metadata(content, meta)
        self.vault.create(rel, "file", raw)
        self._mark_write(rel)
        self._clear_indexes(rel)
        self._publish_templates_changed()
        return self._custom_detail(target, include_content=True)

    def update_custom(self, template_id: str, changes: dict[str, Any]) -> dict:
        path = self._custom_path_from_id(template_id, must_exist=True)
        current = self._custom_detail(path, include_content=True)
        content = changes.get("content", current["content"])
        if not isinstance(content, str):
            raise VaultError("模板正文必须是字符串")
        category = self._path_category(path)
        meta = {
            "title": self._clean_text(changes.get("title", current["title"])) or current["title"],
            "description": self._clean_text(changes.get("description", current["description"])),
            "category": category,
            "tags": self._clean_tags(changes.get("tags", current["tags"])),
            "icon": self._clean_text(changes.get("icon", current["icon"])) or "file-text",
        }
        rel = path.relative_to(self.vault.root).as_posix()
        raw = self._with_template_metadata(content, meta)
        self.vault.write_markdown(rel, raw, None, on_before_write=self._snapshot_callback)
        self._mark_write(rel)
        self._clear_indexes(rel)
        self._publish_templates_changed()
        return self._custom_detail(path, include_content=True)

    def move_custom(self, template_id: str, *, name: str | None = None, category: str | None = None) -> dict:
        src_path = self._custom_path_from_id(template_id, must_exist=True)
        src_rel = src_path.relative_to(self.vault.root).as_posix()
        dst_category = self._path_category(src_path) if category is None else self._normalize_category(category)
        filename = src_path.name if name is None else self._markdown_name(name)
        dst_rel = self._custom_rel(dst_category, filename)
        if dst_rel == src_rel:
            return self._custom_detail(src_path, include_content=True)

        self.vault.move(src_rel, dst_rel)
        dst_path = resolve_in_root(self.vault.root, dst_rel)
        self._mark_write(src_rel)
        self._mark_write(dst_rel)
        self._clear_indexes(src_rel)
        self._clear_indexes(dst_rel)

        # category 是路径语义；同步 frontmatter，保证下载/再次导入时信息一致。
        detail = self._custom_detail(dst_path, include_content=True)
        raw = self._with_template_metadata(detail["content"], {
            "title": detail["title"],
            "description": detail["description"],
            "category": dst_category,
            "tags": detail["tags"],
            "icon": detail["icon"],
        })
        self.vault.write_markdown(dst_rel, raw, None)
        self._mark_write(dst_rel)
        self._publish_templates_changed()
        return self._custom_detail(dst_path, include_content=True)

    def copy_template(self, template_id: str, *, name: str | None = None, category: str | None = None) -> dict:
        source = self.detail(template_id)
        if category is None:
            dst_category = source.get("category", "")
        else:
            dst_category = self._normalize_category(category)
        filename = self._markdown_name(name or source["title"])
        rel = self._custom_rel(dst_category, filename)
        if resolve_in_root(self.vault.root, rel).exists():
            if name is not None:
                raise VaultError(f"模板已存在：{rel}")
            rel = self._unique_rel(dst_category, Path(filename).stem)
            filename = PurePosixPath(rel).name
        return self.create_custom(
            name=filename,
            title=source["title"],
            description=source["description"],
            category=dst_category,
            tags=source["tags"],
            icon=source["icon"],
            content=source["content"],
        )

    def delete_custom(self, template_id: str) -> None:
        path = self._custom_path_from_id(template_id, must_exist=True)
        rel = path.relative_to(self.vault.root).as_posix()
        self.vault.delete(rel)
        self._mark_write(rel)
        self._clear_indexes(rel)
        self._publish_templates_changed()

    # ---------- 分类 ----------

    def create_category(self, name: str) -> dict:
        category = self._normalize_category(name)
        if not category:
            raise VaultError("分类名称不能为空")
        rel = f"{TEMPLATES_DIR}/{category}"
        path = resolve_in_root(self.vault.root, rel)
        if path.exists():
            raise VaultError(f"分类已存在：{category}")
        path.mkdir(parents=True, exist_ok=False)
        self._mark_write(rel)
        self._publish_templates_changed()
        return {"name": category}

    def rename_category(self, name: str, new_name: str) -> dict:
        category = self._normalize_category(name)
        new_category = self._normalize_category(new_name)
        if not category or not new_category:
            raise VaultError("分类名称不能为空")
        src_rel = f"{TEMPLATES_DIR}/{category}"
        dst_rel = f"{TEMPLATES_DIR}/{new_category}"
        src = resolve_in_root(self.vault.root, src_rel)
        if not src.is_dir():
            raise NotFoundError(f"分类不存在：{category}")
        self.vault.move(src_rel, dst_rel)
        self._mark_write(src_rel)
        self._mark_write(dst_rel)
        dst = resolve_in_root(self.vault.root, dst_rel)
        for path in self._iter_custom_paths(dst):
            detail = self._custom_detail(path, include_content=True)
            rel = path.relative_to(self.vault.root).as_posix()
            raw = self._with_template_metadata(detail["content"], {
                "title": detail["title"],
                "description": detail["description"],
                "category": self._path_category(path),
                "tags": detail["tags"],
                "icon": detail["icon"],
            })
            self.vault.write_markdown(rel, raw, None)
            self._mark_write(rel)
            self._clear_indexes(rel)
        self._clear_indexes(src_rel)
        self._publish_templates_changed()
        return {"name": new_category}

    def delete_category(self, name: str, *, force: bool = False) -> None:
        category = self._normalize_category(name)
        if not category:
            raise VaultError("分类名称不能为空")
        rel = f"{TEMPLATES_DIR}/{category}"
        path = resolve_in_root(self.vault.root, rel)
        if not path.is_dir():
            raise NotFoundError(f"分类不存在：{category}")
        if any(path.iterdir()) and not force:
            raise VaultError("分类非空；如需同时删除其中模板，请使用 force=true")
        if force:
            self.vault.delete(rel)
            self._clear_indexes(rel)
        else:
            path.rmdir()
        self._mark_write(rel)
        self._publish_templates_changed()

    # ---------- 应用 ----------

    def apply(self, template_id: str, target_path: str, title: str | None = None) -> str:
        rel = normalize_rel(target_path)
        if is_templates_rel(rel) or is_hidden_rel(rel) or rel.startswith(".trash/"):
            raise PathError("模板只能应用为普通笔记路径")
        if not rel.lower().endswith(".md"):
            raise VaultError("目标笔记必须使用 .md 扩展名")
        target = resolve_in_root(self.vault.root, rel)
        if target.exists():
            raise VaultError(f"已存在同名文件或目录：{rel}")

        detail = self.detail(template_id)
        now = datetime.now().astimezone()
        values = {
            "title": title.strip() if isinstance(title, str) and title.strip() else Path(rel).stem,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "datetime": now.isoformat(timespec="minutes"),
        }
        content = _PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], detail["content"])

        self.vault.create(rel, "file", content)
        self._mark_write(rel)
        try:
            if self.indexer is not None:
                self.indexer.index_file(rel)
            if self.rag is not None:
                self.rag.reindex_file(rel, content)
        except Exception:
            # 文件 + 两套派生索引作为一个应用动作：索引失败时撤销新文件。
            try:
                target.unlink(missing_ok=True)
                if self.indexer is not None:
                    self.indexer.delete_path(rel)
                if self.rag is not None:
                    self.rag.delete_file(rel)
            finally:
                raise
        self._publish_tree_changed()
        return rel

    # ---------- 导入 / 导出 ----------

    def import_markdown(self, files: list[tuple[str, bytes]], *, category: str = "", strategy: str = "skip") -> dict:
        if strategy not in {"skip", "rename", "overwrite"}:
            raise VaultError(f"未知冲突策略：{strategy}")
        if not files:
            raise VaultError("至少上传一个 Markdown 文件")
        if sum(len(data) for _, data in files) > _MAX_IMPORT_TOTAL:
            raise VaultError("模板文件总大小超过 20MB 上限")

        prepared: list[tuple[str, str, dict[str, Any]]] = []
        query_category = self._normalize_category(category) if category.strip() else ""
        for original_name, data in files:
            if len(data) > _MAX_IMPORT_FILE:
                raise VaultError(f"模板文件超过 4MB 上限：{original_name}")
            filename = self._uploaded_markdown_name(original_name)
            try:
                raw = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise VaultError(f"模板不是有效的 UTF-8：{original_name}") from exc
            inferred = self._infer_metadata(raw, Path(filename).stem)
            inferred_category = self._normalize_category(str(inferred.get("declared_category") or ""))
            dst_category = query_category or inferred_category
            prepared.append((filename, raw, inferred | {"category": dst_category}))

        imported: list[dict] = []
        skipped: list[str] = []
        renamed = 0
        for filename, content, meta in prepared:
            dst_category = meta["category"]
            rel = self._custom_rel(dst_category, filename)
            target = resolve_in_root(self.vault.root, rel)
            if target.exists():
                if strategy == "skip":
                    skipped.append(filename)
                    continue
                if strategy == "rename":
                    rel = self._unique_rel(dst_category, Path(filename).stem)
                    target = resolve_in_root(self.vault.root, rel)
                    renamed += 1
                else:
                    raw = self._with_template_metadata(content, meta)
                    self.vault.write_markdown(rel, raw, None, on_before_write=self._snapshot_callback)
                    self._mark_write(rel)
                    self._clear_indexes(rel)
                    imported.append(self._custom_detail(target, include_content=True))
                    continue

            raw = self._with_template_metadata(content, meta)
            self.vault.create(rel, "file", raw)
            self._mark_write(rel)
            self._clear_indexes(rel)
            imported.append(self._custom_detail(target, include_content=True))

        if imported:
            self._publish_templates_changed()
        return {
            "imported": len(imported),
            "skipped": len(skipped),
            "renamed": renamed,
            "templates": imported,
        }

    def export_one(self, template_id: str) -> tuple[str, io.BytesIO]:
        builtin = _BUILTIN_BY_ID.get(template_id)
        if builtin is not None:
            detail = self._builtin_detail(builtin, include_content=True)
            raw = self._with_template_metadata(detail["content"], detail)
            filename = self._markdown_name(detail["title"])
        else:
            path = self._custom_path_from_id(template_id, must_exist=True)
            filename = path.name
            raw = path.read_text(encoding="utf-8-sig")
        return filename, io.BytesIO(raw.encode("utf-8"))

    def export_all(self) -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in self._iter_custom_paths():
                arcname = path.relative_to(self.templates_root).as_posix()
                zf.writestr(arcname, path.read_bytes())
        buf.seek(0)
        return buf

    # ---------- 内部 ----------

    def _builtin_detail(self, item: dict[str, Any], *, include_content: bool) -> dict:
        result = {
            "id": f"builtin:{item['slug']}",
            "source": "builtin",
            "title": item["title"],
            "description": item["description"],
            "category": item["category"],
            "tags": list(item["tags"]),
            "icon": item["icon"],
            "updated_at": "",
        }
        if include_content:
            result["content"] = item["content"]
        return result

    def _custom_detail(self, path: Path, *, include_content: bool) -> dict:
        rel = path.relative_to(self.vault.root).as_posix()
        # 再走一次 path_guard；遍历结果也不绕过模板目录边界。
        self._custom_rel_from_path(rel)
        raw = path.read_text(encoding="utf-8-sig")
        meta = self._infer_metadata(raw, path.stem)
        stat = path.stat()
        result = {
            "id": f"custom:{rel}",
            "source": "custom",
            "title": meta["title"],
            "description": meta["description"],
            "category": self._path_category(path),
            "tags": meta["tags"],
            "icon": meta["icon"],
            "path": rel,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
        if include_content:
            result["content"] = self._without_template_metadata(raw)
        return result

    def _custom_path_from_id(self, template_id: str, *, must_exist: bool) -> Path:
        if not isinstance(template_id, str) or not template_id.startswith("custom:"):
            if isinstance(template_id, str) and template_id.startswith("builtin:"):
                raise VaultError("内置模板为只读模板")
            raise PathError("无效的模板 ID")
        rel = self._custom_rel_from_path(template_id[len("custom:"):])
        path = resolve_in_root(self.vault.root, rel)
        if must_exist and not path.is_file():
            raise NotFoundError(f"模板不存在：{template_id}")
        return path

    @staticmethod
    def _custom_rel_from_path(rel: str) -> str:
        rel = normalize_rel(rel)
        parts = PurePosixPath(rel).parts
        if len(parts) < 2 or parts[0] != TEMPLATES_DIR or not rel.lower().endswith(".md"):
            raise PathError("自定义模板必须位于 templates/ 且使用 .md 扩展名")
        if is_hidden_rel(rel):
            raise PathError("模板路径不允许包含隐藏目录")
        return rel

    def _iter_custom_paths(self, root: Path | None = None):
        root = root or self.templates_root
        if not root.exists():
            return
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and not (Path(dirpath) / name).is_symlink()
            ]
            for name in sorted(filenames, key=str.casefold):
                if name.startswith(".") or not name.lower().endswith(".md"):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                try:
                    self._custom_rel_from_path(path.relative_to(self.vault.root).as_posix())
                except PathError:
                    continue
                yield path

    def _normalize_category(self, category: str) -> str:
        if not isinstance(category, str):
            raise PathError("分类名称必须是字符串")
        category = category.strip().strip("/").replace("\\", "/")
        if not category:
            return ""
        rel = normalize_rel(f"{TEMPLATES_DIR}/{category}")
        parts = PurePosixPath(rel).parts
        if parts[0] != TEMPLATES_DIR or any(part.startswith(".") for part in parts[1:]):
            raise PathError("分类必须位于 templates/ 且不能是隐藏目录")
        return PurePosixPath(*parts[1:]).as_posix()

    @staticmethod
    def _markdown_name(name: str) -> str:
        clean = validate_name(name)
        if not clean.lower().endswith(".md"):
            clean += ".md"
        return validate_name(clean)

    def _uploaded_markdown_name(self, name: str) -> str:
        clean = PurePosixPath((name or "template.md").replace("\\", "/")).name
        clean = validate_name(clean)
        if not clean.lower().endswith(".md"):
            raise VaultError(f"只支持 Markdown 模板：{name}")
        return clean

    @staticmethod
    def _custom_rel(category: str, filename: str) -> str:
        rel = f"{TEMPLATES_DIR}/{category}/{filename}" if category else f"{TEMPLATES_DIR}/{filename}"
        return TemplateService._custom_rel_from_path(rel)

    def _unique_rel(self, category: str, stem: str) -> str:
        stem = validate_name(stem)
        index = 1
        while True:
            suffix = " (1)" if index == 1 else f" ({index})"
            rel = self._custom_rel(category, self._markdown_name(stem + suffix))
            if not resolve_in_root(self.vault.root, rel).exists():
                return rel
            index += 1

    def _path_category(self, path: Path) -> str:
        parent = path.parent.relative_to(self.templates_root).as_posix()
        return "" if parent == "." else parent

    def _infer_metadata(self, raw: str, fallback: str) -> dict[str, Any]:
        data, _body = self._frontmatter_parts(raw)
        data = data or {}
        content = self._without_template_metadata(raw)
        title_value = data.get("template_title")
        title = self._clean_text(title_value) or doc_title(content, fallback)
        description = self._clean_text(data.get("template_description"))
        if not description:
            description = self._clean_text(data.get("description")) or self._infer_description(content)
        tag_value = data.get("template_tags", data.get("tags", []))
        icon = self._clean_text(data.get("template_icon")) or "file-text"
        declared_category = self._clean_text(data.get("template_category", data.get("category", "")))
        return {
            "title": title,
            "description": description,
            "tags": self._clean_tags(tag_value),
            "icon": icon,
            "declared_category": declared_category,
        }

    @staticmethod
    def _frontmatter_parts(text: str) -> tuple[dict[str, Any] | None, str]:
        fm = parse_frontmatter(text)
        if fm.end == 0:
            return None, text
        try:
            parsed = yaml.safe_load(fm.raw) or {}
        except yaml.YAMLError:
            return None, text
        if not isinstance(parsed, dict):
            return None, text
        lines = text.split("\n")
        return parsed, "\n".join(lines[fm.end + 1:])

    def _without_template_metadata(self, text: str) -> str:
        data, body = self._frontmatter_parts(text)
        if data is None:
            return text
        remaining = {
            key: value for key, value in data.items()
            if not (isinstance(key, str) and key.startswith("template_"))
        }
        if not remaining:
            return body.lstrip("\n")
        return self._serialize_frontmatter(remaining, body)

    def _with_template_metadata(self, content: str, meta: dict[str, Any]) -> str:
        clean_content = self._without_template_metadata(content)
        existing, body = self._frontmatter_parts(clean_content)
        ordinary = existing or {}
        data: dict[str, Any] = {
            "template_title": self._clean_text(meta.get("title")),
            "template_description": self._clean_text(meta.get("description")),
            "template_category": self._clean_text(meta.get("category")),
            "template_tags": self._clean_tags(meta.get("tags", [])),
            "template_icon": self._clean_text(meta.get("icon")) or "file-text",
        }
        data.update({key: value for key, value in ordinary.items() if key not in _TEMPLATE_KEYS})
        return self._serialize_frontmatter(data, body if existing is not None else clean_content)

    @staticmethod
    def _serialize_frontmatter(data: dict[str, Any], body: str) -> str:
        yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip()
        return f"---\n{yaml_text}\n---\n{body.lstrip(chr(10))}"

    @staticmethod
    def _infer_description(content: str) -> str:
        body = content
        fm = parse_frontmatter(content)
        if fm.end:
            body = "\n".join(content.split("\n")[fm.end + 1:])
        in_fence = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence or not stripped:
                continue
            if stripped.startswith(("#", "- ", "* ", "+ ", ">", "|")):
                continue
            if re.match(r"^\d+[.)]\s", stripped):
                continue
            return stripped[:160]
        return ""

    @staticmethod
    def _clean_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _clean_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = []
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                continue
            tag = item.strip().lstrip("#")
            if tag and tag not in seen:
                seen.add(tag)
                result.append(tag)
        return result

    def _snapshot_callback(self, rel: str, text: str, _etag=None) -> None:
        if self.history is not None:
            self.history.save_snapshot(rel, text)

    def _mark_write(self, rel: str) -> None:
        if self.watcher is not None:
            self.watcher.mark_self_write(rel)

    def _clear_indexes(self, rel: str) -> None:
        if self.indexer is not None:
            self.indexer.delete_path(rel)
        if self.rag is not None:
            self.rag.delete_path(rel)

    def _publish_templates_changed(self) -> None:
        if self.event_hub is not None:
            self.event_hub.publish({"type": "templates_changed"})

    def _publish_tree_changed(self) -> None:
        if self.event_hub is not None:
            self.event_hub.publish({"type": "tree_changed"})
