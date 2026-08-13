"""SQLite 数据库：应用状态与可重建知识索引。

知识正文的唯一权威副本在 Vault 内；数据库只存派生索引和运行状态，
删除 index.db 后可通过 Indexer.rebuild() 完整恢复。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files_meta (
    path        TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    mtime_ns    INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    sha1        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    indexed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS headings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT NOT NULL,
    level       INTEGER NOT NULL,
    text        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    line        INTEGER NOT NULL,
    FOREIGN KEY(file_path) REFERENCES files_meta(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_headings_file ON headings(file_path);

CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    target_raw  TEXT NOT NULL,
    target_path TEXT,
    anchor      TEXT NOT NULL DEFAULT '',
    alias       TEXT NOT NULL DEFAULT '',
    link_type   TEXT NOT NULL,
    line        INTEGER NOT NULL DEFAULT 0,
    context     TEXT NOT NULL DEFAULT '',
    resolved    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(source_path) REFERENCES files_meta(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_path);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_path);
CREATE INDEX IF NOT EXISTS idx_links_raw ON links(target_raw);

CREATE TABLE IF NOT EXISTS file_tags (
    file_path   TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY(file_path, tag),
    FOREIGN KEY(file_path) REFERENCES files_meta(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    heading       TEXT NOT NULL DEFAULT '',
    line_start    INTEGER NOT NULL,
    line_end      INTEGER NOT NULL,
    text          TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    ai_indexed    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(file_path) REFERENCES files_meta(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id   INTEGER PRIMARY KEY,
    model      TEXT NOT NULL,
    dims       INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS index_failures (
    path        TEXT NOT NULL,
    subsystem   TEXT NOT NULL,
    error       TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY(path, subsystem)
);

CREATE TABLE IF NOT EXISTS doc_ocr (
    file_path   TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending',
    progress    INTEGER NOT NULL DEFAULT 0,
    chars       INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(file_path) REFERENCES files_meta(path) ON DELETE CASCADE
);
"""


def _ensure_fts(conn: sqlite3.Connection) -> str:
    """创建全文索引，优先 trigram（中文友好），失败时回退 unicode61。

    返回实际 tokenizer 名。files_fts 是 contentless 独立索引，由 Indexer 维护。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='files_fts'"
    ).fetchone()
    if row:
        sql = row[0] or ""
        return "trigram" if "trigram" in sql else "unicode61"
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE files_fts USING fts5(path UNINDEXED, title, body, tags, tokenize='trigram')"
        )
        return "trigram"
    except sqlite3.OperationalError:
        conn.execute(
            "CREATE VIRTUAL TABLE files_fts USING fts5(path UNINDEXED, title, body, tags, tokenize='unicode61')"
        )
        return "unicode61"


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self.fts_tokenizer = "unknown"

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_files_meta(conn)
            self._migrate_doc_ocr(conn)
            self.fts_tokenizer = _ensure_fts(conn)
            # 只检测版本，不写回；重建成功后由 mark_schema_current() 标记
            self.needs_rebuild = self._check_schema_version(conn)

    @staticmethod
    def _migrate_doc_ocr(conn: sqlite3.Connection) -> None:
        """兼容：早期 doc_ocr 表缺 text 列（识别结果文本），补齐。"""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(doc_ocr)")}
        if "text" not in columns:
            conn.execute("ALTER TABLE doc_ocr ADD COLUMN text TEXT NOT NULL DEFAULT ''")

    def _check_schema_version(self, conn: sqlite3.Connection) -> bool:
        """检测 schema 版本是否需要重建；仅在需要时返回 True。

        - 首次（无版本记录）：需要重建（此时不写回）
        - 版本低于程序版本：需要重建
        - 版本高于程序版本：拒绝降级，明确报错
        - 损坏版本：明确报错
        """
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if row is None:
            return True
        raw = row[0]
        try:
            current = int(raw)
        except (TypeError, ValueError):
            raise RuntimeError(
                f"数据库 schema 版本损坏：{raw!r}；请删除 data/index.db 后重启"
            ) from None
        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 schema 版本 {current} 高于程序支持的 {_SCHEMA_VERSION}，"
                "拒绝降级；请升级程序，或删除 data/index.db 后重启"
            )
        return current != _SCHEMA_VERSION

    def mark_schema_current(self) -> None:
        """仅在重建/迁移成功后调用：写回当前版本。"""
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
                (str(_SCHEMA_VERSION),),
            )

    @staticmethod
    def _migrate_files_meta(conn: sqlite3.Connection) -> None:
        """兼容 M1 数据库：为已有 files_meta 添加新列。"""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(files_meta)")}
        additions = {
            "title": "TEXT NOT NULL DEFAULT ''",
            "tags": "TEXT NOT NULL DEFAULT ''",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE files_meta ADD COLUMN {name} {sql_type}")
        # 旧 schema 的 indexed_at 可为空；索引器重建时会补齐
