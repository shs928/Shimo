"""Vault 派生索引：FTS5 全文搜索、标签、链接、反链与局部图谱。

索引完全可重建。每个文件的更新在一个 SQLite 事务内完成，先删除旧派生数据，
再插入新元数据、FTS 行、标签和链接。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..db import Database
from .links import parse_links, resolve_markdown_target
from .metadata import doc_title, parse_frontmatter, parse_headings
from .path_guard import is_templates_rel
from .vault import Vault, VaultError

_TAG_RE = re.compile(r"(?<![\w/])#([\w\u4e00-\u9fff/-]+)")


def query_tokens(query: str) -> list[str]:
    """从自然语言问题中抽取检索关键词（去重、限量）。

    - 字母数字串（长度 ≥2）；
    - CJK 连续段：整段 + 2/3-gram（保证"送礼有哪些讲究"能拆出"送礼"命中笔记）。
    """
    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z0-9_]{2,}", query):
        tokens.append(word)
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        tokens.append(seg)
        for n in (2, 3):
            tokens.extend(seg[i : i + n] for i in range(len(seg) - n + 1))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:16]


class Indexer:
    def __init__(self, vault: Vault, db: Database):
        self.vault = vault
        self.db = db
        self._lock = threading.RLock()

    def rebuild(self) -> dict:
        """从 Vault 全量重建普通派生索引（links/tags/FTS/headings）。

        **不删除 files_meta**：chunks/embeddings（RAG 数据）通过外键级联绑定
        files_meta，清空 files_meta 会连带清掉 RAG 索引。改为：
        - 清空并重建普通派生表；
        - 对现有 Markdown upsert 元数据（保留有效父记录）；
        - 只删除 Vault 已不存在的 stale Markdown 行；
        - PDF/Word/TXT/CSV 占位元数据（非 .md）保留。
        整个重建在单个事务内完成，失败自动回滚。

        批量解析后统一写库、统一解析链接，避免逐文件触发
        resolve_all_links 造成 O(N²)。
        """
        indexed = 0
        failed: list[dict] = []
        prepared: list[tuple[str, str, str, str, int, str, int]] = []
        with self._lock:
            for path in self.iter_markdown_paths():
                try:
                    fc = self.vault.read_markdown(path)
                    fm = parse_frontmatter(fc.content)
                    title = doc_title(fc.content, Path(path).stem)
                    tags = _extract_tags(fc.content, fm.data)
                    prepared.append(
                        (
                            path,
                            title,
                            fc.content,
                            ",".join(tags),
                            fc.size,
                            hashlib.sha1(fc.content.encode("utf-8")).hexdigest(),
                            fc.mtime_ns,
                        )
                    )
                    indexed += 1
                except Exception as exc:  # 单文件失败不阻断全库
                    failed.append({"path": path, "error": str(exc)})

            with self.db.connect() as conn:
                conn.execute("DELETE FROM links")
                conn.execute("DELETE FROM file_tags")
                conn.execute("DELETE FROM files_fts")
                conn.execute("DELETE FROM headings")
                for rel, title, text, tags, size, sha1, mtime_ns in prepared:
                    _insert_doc(conn, self.vault.root, rel, title, text, tags, size, sha1, mtime_ns)
                # 清理 stale：仅删除已从磁盘消失的 Markdown 行；
                # 解析失败（failed）的文件保留 files_meta 与其 RAG 数据，
                # 避免瞬时读取/解析错误被误判为删除而级联清空 chunks/embeddings。
                existing = {row[0] for row in conn.execute("SELECT path FROM files_meta")}
                live = {p[0] for p in prepared}
                failed_paths = {f["path"] for f in failed}
                for path in existing:
                    # 模板目录永不进入普通/RAG 索引；重建顺带清理历史遗留记录。
                    if is_templates_rel(path):
                        conn.execute("DELETE FROM files_meta WHERE path=?", (path,))
                    elif path not in live and path not in failed_paths and path.lower().endswith(".md"):
                        conn.execute("DELETE FROM files_meta WHERE path=?", (path,))
            self.resolve_all_links()
        return {"indexed": indexed, "failed": failed, "tokenizer": self.db.fts_tokenizer}

    def iter_markdown_paths(self):
        root = self.vault.root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and not (
                    Path(dirpath) == root and d.casefold() == "templates"
                )
            ]
            for name in filenames:
                if name.lower().endswith(".md"):
                    yield (Path(dirpath) / name).relative_to(root).as_posix()

    def index_file(self, rel: str) -> None:
        if is_templates_rel(rel):
            self.delete_path(rel)
            return
        if not rel.lower().endswith(".md"):
            return
        fc = self.vault.read_markdown(rel)
        text = fc.content
        sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
        fm = parse_frontmatter(text)
        title = doc_title(text, Path(rel).stem)
        tags = _extract_tags(text, fm.data)

        with self._lock, self.db.connect() as conn:
            conn.execute("DELETE FROM links WHERE source_path = ?", (rel,))
            conn.execute("DELETE FROM file_tags WHERE file_path = ?", (rel,))
            conn.execute("DELETE FROM files_fts WHERE path = ?", (rel,))
            conn.execute("DELETE FROM headings WHERE file_path = ?", (rel,))
            _insert_doc(conn, self.vault.root, rel, title, text, ",".join(tags), fc.size, sha1, fc.mtime_ns)

        # 新文件可能让其他文件的未解析链接变为可解析，做轻量重解析。
        self.resolve_all_links()

    def delete_path(self, rel: str) -> None:
        """删除文件或目录前缀的索引，并重解析受影响链接。

        前缀匹配在 Python 中精确判断，不使用未转义的 LIKE。
        """
        with self._lock, self.db.connect() as conn:
            all_paths = [row[0] for row in conn.execute("SELECT path FROM files_meta")]
            prefix = rel.rstrip("/") + "/"
            affected = [p for p in all_paths if p == rel or p.startswith(prefix)]
            for path in affected:
                conn.execute("DELETE FROM files_fts WHERE path = ?", (path,))
                conn.execute("DELETE FROM links WHERE source_path = ?", (path,))
                conn.execute("DELETE FROM file_tags WHERE file_path = ?", (path,))
                conn.execute("DELETE FROM headings WHERE file_path = ?", (path,))
                conn.execute("DELETE FROM files_meta WHERE path = ?", (path,))
        self.resolve_all_links()

    def move_path(self, src: str, dst: str) -> None:
        """文件系统移动后，将旧前缀索引删除并索引新路径。"""
        self.delete_path(src)
        target = self.vault.root / dst
        if is_templates_rel(dst):
            return
        if target.is_file() and dst.lower().endswith(".md"):
            self.index_file(dst)
        elif target.is_dir():
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    if name.lower().endswith(".md"):
                        rel = (Path(dirpath) / name).relative_to(self.vault.root).as_posix()
                        self.index_file(rel)

    def resolve_all_links(self) -> int:
        """重新解析所有链接目标，返回发生变化的记录数。"""
        changed = 0
        with self._lock, self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id,source_path,target_raw,target_path FROM links"
            ).fetchall()
            for row in rows:
                target = resolve_markdown_target(
                    self.vault.root, row["source_path"], row["target_raw"]
                )
                if target != row["target_path"]:
                    conn.execute(
                        "UPDATE links SET target_path=?, resolved=? WHERE id=?",
                        (target, int(target is not None), row["id"]),
                    )
                    changed += 1
        return changed

    def search(self, query: str, limit: int = 30) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        conn = self.db.connect()
        # trigram 需要至少 3 个字符；中文/英文短词显式走 LIKE 降级，避免静默漏检。
        if len(query) < 3:
            like = f"%{query}%"
            rows = conn.execute(
                """SELECT path,title,substr(body,1,240) AS snippet,0 AS rank
                   FROM files_fts
                   WHERE path LIKE ? OR title LIKE ? OR body LIKE ? ESCAPE '\\'
                   ORDER BY title LIMIT ?""",
                (like, like, like, limit),
            ).fetchall()
            return [dict(row) for row in rows if not is_templates_rel(row["path"])]

        # 自然语言问题拆关键词后 OR 查询：整句 phrase 匹配对长问题召回过低
        tokens = query_tokens(query)
        if not tokens:
            tokens = [query]
        fts_query = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        try:
            rows = conn.execute(
                """SELECT path,title,
                          snippet(files_fts,2,'<mark>','</mark>',' … ',32) AS snippet,
                          bm25(files_fts,10.0,5.0,1.0) AS rank
                   FROM files_fts WHERE files_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = conn.execute(
                """SELECT path,title,substr(body,1,240) AS snippet,0 AS rank
                   FROM files_meta WHERE title LIKE ? OR body LIKE ? LIMIT ?""",
                (like, like, limit),
            ).fetchall()
        # 剥离 FTS snippet 的 <mark> 标记，前端基于纯文本高亮
        out = []
        for row in rows:
            if is_templates_rel(row["path"]):
                continue
            d = dict(row)
            d["snippet"] = (d.get("snippet") or "").replace("<mark>", "").replace("</mark>", "")
            out.append(d)
        return out

    def backlinks(self, target_path: str) -> list[dict]:
        rows = self.db.connect().execute(
            """SELECT l.source_path,m.title,l.anchor,l.alias,l.link_type,l.line,l.context
               FROM links l LEFT JOIN files_meta m ON m.path=l.source_path
               WHERE l.target_path=? ORDER BY l.source_path,l.line""",
            (target_path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def outgoing(self, source_path: str) -> list[dict]:
        rows = self.db.connect().execute(
            """SELECT target_raw,target_path,anchor,alias,link_type,line,context,resolved
               FROM links WHERE source_path=? ORDER BY line""",
            (source_path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def graph(self, path: str, depth: int = 1) -> dict:
        """局部图谱：当前节点 + 直接入链/出链；首版固定 depth=1。"""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def add_node(p: str) -> None:
            if p in nodes:
                return
            row = self.db.connect().execute(
                "SELECT path,title FROM files_meta WHERE path=?", (p,)
            ).fetchone()
            nodes[p] = {"id": p, "label": row["title"] if row else Path(p).stem}

        add_node(path)
        for link in self.outgoing(path):
            target = link.get("target_path")
            if target:
                add_node(target)
                edges.append({"source": path, "target": target})
        for link in self.backlinks(path):
            source = link["source_path"]
            add_node(source)
            edges.append({"source": source, "target": path})
        unique_edges = list({(e["source"], e["target"]): e for e in edges}.values())
        return {"nodes": list(nodes.values()), "edges": unique_edges}

    def stats(self) -> dict:
        conn = self.db.connect()
        return {
            "files": conn.execute("SELECT count(*) FROM files_meta").fetchone()[0],
            "links": conn.execute("SELECT count(*) FROM links").fetchone()[0],
            "unresolved_links": conn.execute(
                "SELECT count(*) FROM links WHERE resolved=0 AND link_type IN ('wiki','markdown')"
            ).fetchone()[0],
            "tokenizer": self.db.fts_tokenizer,
        }


def _insert_doc(
    conn: sqlite3.Connection,
    root: Path,
    rel: str,
    title: str,
    text: str,
    tags_str: str,
    size: int,
    sha1: str,
    mtime_ns: int,
) -> None:
    """在单个事务内写入一个文档的派生数据：元数据、FTS、标签、链接、标题大纲。"""
    indexed_at = datetime.now(timezone.utc).isoformat()
    fm = parse_frontmatter(text)
    headings = parse_headings(text)
    parsed_links = parse_links(text)

    conn.execute(
        """INSERT INTO files_meta(path,title,mtime_ns,size,sha1,tags,indexed_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             title=excluded.title,mtime_ns=excluded.mtime_ns,size=excluded.size,
             sha1=excluded.sha1,tags=excluded.tags,indexed_at=excluded.indexed_at""",
        (rel, title, mtime_ns, size, sha1, tags_str, indexed_at),
    )
    conn.execute(
        "INSERT INTO files_fts(path,title,body,tags) VALUES(?,?,?,?)",
        (rel, title, text, tags_str.replace(",", " ")),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO file_tags(file_path,tag) VALUES(?,?)",
        [(rel, tag) for tag in tags_str.split(",") if tag],
    )
    conn.executemany(
        "INSERT INTO headings(file_path,level,text,slug,line) VALUES(?,?,?,?,?)",
        [(rel, h.level, h.text, h.slug, h.line) for h in headings],
    )
    for link in parsed_links:
        if link.link_type in ("image", "embed"):
            continue
        target = resolve_markdown_target(root, rel, link.target_raw)
        conn.execute(
            """INSERT INTO links(
                 source_path,target_raw,target_path,anchor,alias,link_type,line,context,resolved
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                rel, link.target_raw, target, link.anchor, link.alias,
                link.link_type, link.line, link.context, int(target is not None),
            ),
        )


def _extract_tags(text: str, frontmatter: dict) -> list[str]:
    tags: set[str] = set()
    fm_tags = frontmatter.get("tags", [])
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",")]
    if isinstance(fm_tags, list):
        for tag in fm_tags:
            if isinstance(tag, str) and tag.strip():
                tags.add(tag.strip().lstrip("#"))

    # 跳过 front matter 后扫描正文 hashtag。
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            body = parts[2]
    for m in _TAG_RE.finditer(body):
        tags.add(m.group(1))
    return sorted(tags)
