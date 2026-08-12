"""2.6 schema version 正确写回：首次、旧版、失败、不兼容未来版本。"""
from __future__ import annotations

import pytest

from app.db import Database


def _connect(db: Database):
    return db.connect()


def test_first_run_needs_rebuild_and_marks_after(client):
    """首次启动：needs_rebuild=True；mark_schema_current() 后版本写回。"""
    db = client.app.state.db
    assert db.needs_rebuild is True  # 首次（无版本记录）→ 需要重建
    # lifespan 已完成 rebuild + mark
    row = _connect(db).execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    assert row[0] == "3"
    # 重建后再次初始化：不再需要重建
    db2 = Database(db.path)
    db2.initialize()
    assert db2.needs_rebuild is False


def test_old_version_needs_rebuild(client):
    db = client.app.state.db
    with _connect(db) as conn:
        conn.execute("UPDATE schema_meta SET value='2' WHERE key='version'")
    db2 = Database(db.path)
    db2.initialize()
    assert db2.needs_rebuild is True
    db2.mark_schema_current()
    db3 = Database(db.path)
    db3.initialize()
    assert db3.needs_rebuild is False


def test_initialize_never_writes_version(client):
    """initialize() 只检测版本；写回只能通过 mark_schema_current()（重建成功路径）。"""
    db = client.app.state.db
    with _connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
            ("2",),
        )
    # 模拟 rebuild 失败：只 initialize（检测）不 mark → 版本保持旧值
    db2 = Database(db.path)
    db2.initialize()
    assert db2.needs_rebuild is True
    row = _connect(db).execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    assert row[0] == "2"  # 未写回
    # 重建成功后显式标记
    db2.mark_schema_current()
    row = _connect(db).execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    assert row[0] == "3"


def test_future_version_refused(client):
    """版本高于程序版本：拒绝降级，明确报错。"""
    db = client.app.state.db
    with _connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
            ("99",),
        )
    with pytest.raises(RuntimeError, match="拒绝降级"):
        Database(db.path).initialize()


def test_corrupt_version_refused(client):
    """损坏版本：明确报错。"""
    db = client.app.state.db
    with _connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)",
            ("abc",),
        )
    with pytest.raises(RuntimeError, match="版本损坏"):
        Database(db.path).initialize()
