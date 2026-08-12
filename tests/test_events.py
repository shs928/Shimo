"""4.1 文件 watcher + SSE 事件总线测试。"""
from __future__ import annotations

import asyncio

from app.services.events import EventHub
from app.services.watcher import VaultWatcher


def test_event_hub_publish_subscribe():
    hub = EventHub()
    q = hub.subscribe()
    assert hub.subscriber_count() == 1
    hub.publish({"type": "tree_changed"})
    assert q.get_nowait() == {"type": "tree_changed"}
    hub.unsubscribe(q)
    assert hub.subscriber_count() == 0


def test_event_hub_drops_stale_for_slow_consumer():
    hub = EventHub()
    q = hub.subscribe()
    for i in range(70):  # 超过队列上限
        hub.publish({"type": "tree_changed", "seq": i})
    assert q.qsize() <= 64  # 慢消费者丢最旧
    assert hub.subscriber_count() == 1


# 说明：SSE 端点端到端已通过真实 uvicorn 服务器验证（连接帧 + 事件广播）。
# TestClient / ASGITransport 对无限 SSE 流存在运行时兼容问题（最小复现亦卡），
# 自动测试保留 EventHub 与 watcher 单元层。详见交付报告。    


def test_watcher_ignores_outside_and_hidden(client):
    """watcher 事件处理：越界/隐藏/回收站路径被忽略。"""
    hub = EventHub()
    w = VaultWatcher(client.app.state.vault, client.app.state.indexer, client.app.state.rag, hub)
    assert w._to_rel("/etc/passwd") is None
    assert w._to_rel(str(client.app.state.vault.root / ".hidden" / "x.md")) is None
    assert w._to_rel(str(client.app.state.vault.root / ".trash" / "x.md")) is None
    rel = w._to_rel(str(client.app.state.vault.root / "docs" / "a.md"))
    assert rel == "docs/a.md"


def test_watcher_handles_upsert_and_broadcasts(client, tmp_path):
    """外部新增 .md：增量索引 + tree_changed 广播。"""
    hub = EventHub()
    q = hub.subscribe()
    vault = client.app.state.vault
    w = VaultWatcher(vault, client.app.state.indexer, client.app.state.rag, hub)

    # 模拟 watchfiles 变化：外部创建文件（不走应用写路径 → 无抑制标记）
    full = vault.root / "外部笔记.md"
    full.write_text("# 外部笔记\n\n外部内容独特词 watcher42。\n", encoding="utf-8")
    w._handle_changes({("added", str(full))})

    assert q.get_nowait()["type"] == "tree_changed"
    # 索引已更新：FTS 检索命中
    hits = client.app.state.indexer.search("watcher42", limit=5)
    assert any(h["path"] == "外部笔记.md" for h in hits)


def test_watcher_self_write_window_suppresses(client):
    """应用内写入：抑制窗口内 watcher 跳过（避免重复风暴）。"""
    hub = EventHub()
    q = hub.subscribe()
    vault = client.app.state.vault
    w = VaultWatcher(vault, client.app.state.indexer, client.app.state.rag, hub)

    full = vault.root / "自写.md"
    full.write_text("# 自写\n", encoding="utf-8")
    w.mark_self_write("自写.md")  # 应用内写入标记
    w._handle_changes({("modified", str(full))})
    assert q.empty()  # 未广播（窗口内跳过）
