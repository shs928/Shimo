"""URL / 网页链接导入：SSRF 安全抓取 → 提取正文 → 写入 Vault → 进入 RAG 索引。

- HTML 页面：trafilatura 抽取正文为 Markdown，存为 .md（带 source frontmatter 溯源）。
- PDF 链接：下载原始文件存为 .pdf，复用 doc_parser 解析（扫描件自动进 OCR 队列）。
- 下载复用 safe_download.fetch_page：逐跳 SSRF 校验、Content-Type 白名单、2MB 上限。
"""
from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

from .path_guard import validate_name
from .safe_download import DownloadError, fetch_page
from .vault import VaultError

logger = logging.getLogger(__name__)

_PDF_TYPES = {"application/pdf"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


class UrlImportError(VaultError):
    """URL 导入失败（协议、网络、正文抽取等），路由层映射为 400。"""


def _slug(url: str) -> str:
    """从 URL 生成可读文件名：路径最末段去扩展名，URL 解码，非法字符替换。"""
    name = ""
    path = urlparse(url).path.rstrip("/")
    if path:
        seg = path.rsplit("/", 1)[-1]
        stem = seg.rsplit(".", 1)[0] if "." in seg else seg
        name = unquote(stem).strip()
    for ch in ('<', '>', ':', '"', '|', '?', '*', '\\'):
        name = name.replace(ch, "_")
    name = name.strip(" .")[:80]
    return name or "untitled"


def _html_title(html: str, url: str) -> str:
    """页面标题：trafilatura metadata 优先，回退 <title>，再回退 URL 可读名。"""
    try:
        import trafilatura

        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            return meta.title.strip()
    except Exception:
        pass
    try:
        head = html[:4096]
        low = head.lower()
        start = low.find("<title")
        if start >= 0:
            gt = head.find(">", start)
            lt = head.find("</title>", gt)
            if gt >= 0 and lt > gt:
                title = head[gt + 1 : lt].strip()
                if title:
                    return title
    except Exception:
        pass
    return _slug(url) or "网页"


def extract_page(html: str) -> str:
    """trafilatura 抽取正文为 Markdown；无正文返回空串。"""
    import trafilatura

    return (trafilatura.extract(html, output_format="markdown") or "").strip()


def _suggest_name(url: str, ctype: str) -> str:
    """文件名：URL 末段 slug；PDF 补 .pdf，HTML 补 .md（并去除 slug 里误带的 .pdf）。"""
    if ctype in _PDF_TYPES:
        return _slug(url) + ".pdf"
    base = _slug(url)
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    return base + ".md"


def import_url(
    vault,
    indexer,
    rag,
    ocr_service,
    url: str,
    dir: str = "",
    watcher=None,
    health=None,
) -> dict:
    """URL 导入主流程，返回 {path, name, size, parsed_chars, title, source_url, ocr_status?}。

    步骤：SSRF 校验抓取 → 按 Content-Type 分支（HTML 转 md / PDF 存原始）→
    vault 写盘 → mark_self_write → 与 imports.py 同套索引逻辑入 RAG。
    索引失败不撤销导入，记录到 index_health 供诊断与重试。
    """
    try:
        data, final_url, ctype = fetch_page(url)
    except DownloadError as exc:
        raise UrlImportError(str(exc)) from exc

    source = final_url or url
    title = ""
    if ctype in _PDF_TYPES:
        content = data
        title = _slug(source)
    elif ctype in _HTML_TYPES:
        try:
            html = data.decode("utf-8", errors="replace")
        except Exception as exc:
            raise UrlImportError(f"网页内容解码失败：{exc}") from exc
        md = extract_page(html)
        if not md:
            raise UrlImportError("无法从该网页提取正文（可能是空页或纯 JS 渲染页面）")
        title = _html_title(html, url)
        content = f"---\nsource: {source}\n---\n\n# {title}\n\n{md}".encode("utf-8")
    else:
        # fetch_page 已做 Content-Type 白名单，理论不可达；防御性兜底
        raise UrlImportError(f"不支持的内容类型：{ctype or '未知'}")

    name = _suggest_name(source, ctype)
    rel_dir = dir.strip("/")
    rel = f"{rel_dir}/{name}" if rel_dir else name
    try:
        node = vault.import_file(rel, content)
    except Exception as exc:
        raise UrlImportError(str(exc)) from exc
    if watcher is not None:
        watcher.mark_self_write(node.path)

    parsed_chars = 0
    ocr_status = None
    try:
        if node.path.lower().endswith(".md"):
            fc = vault.read_markdown(node.path)
            indexer.index_file(node.path)
            rag.reindex_file(node.path, fc.content)
            parsed_chars = len(fc.content)
        else:
            text = ocr_service.text_for_index(vault, node.path)
            if text:
                rag.reindex_file(node.path, text)
                parsed_chars = len(text)
            else:
                st = ocr_service.status(node.path)
                ocr_status = (st or {}).get("status")
    except Exception as exc:
        # 索引失败不撤销已成功的导入；记录到 index_failures 供诊断与重试
        if health is not None:
            health.record(node.path, "index", str(exc))
        else:
            logger.warning("URL 导入索引失败（文件已落盘）%s: %s", node.path, exc)

    out = {
        "path": node.path,
        "name": node.name,
        "size": node.size,
        "parsed_chars": parsed_chars,
        "title": title,
        "source_url": source,
    }
    if ocr_status:
        out["ocr_status"] = ocr_status
    return out
