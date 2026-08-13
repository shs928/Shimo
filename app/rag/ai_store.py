"""AI 配置存储：data/ai.json + 系统凭据库（keyring）。

结构 v2（多 Provider）：
- providers[]：OpenAI 兼容服务列表（base_url / models；**api_key 不落盘**，
  由 SecretStore 存系统凭据库，运行时加载注入）
- chat / embedding / rerank / agent：按 provider_id + model 引用
- mcp.servers：外部 MCP server 列表

兼容：读取旧 v1/v2 明文 api_key 时先写入系统凭据库，成功后原子重写 JSON 删除 Key；
环境变量 AI_CHAT_* / AI_EMBED_* 优先且永不落盘。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .provider import ProviderConfig
from .secret_store import SecretStore, SecretStoreError

DEFAULT_TOOLS: dict = {
    "knowledge_search": True,
    "read_note": True,
    "list_notes": True,
    "sql": False,
    "create_note": True,
    "update_note": True,
    "image.analyze": True,
    "image.generate": True,
}

DEFAULT_AGENT_SYSTEM_PROMPT = """你是个人知识库的 AI 助手。回答用户问题时：
- 优先使用 knowledge_search 检索知识库中的笔记，再回答；引用来源（如 [note.md]）。
- 不要编造笔记中没有的信息；不知道就明确说明。
- 需要创建或修改笔记、生成图片时，先向用户确认再执行。
- 回答使用 Markdown 格式。"""


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)

    def config(self, model: str) -> ProviderConfig:
        return ProviderConfig(self.base_url, self.api_key, model, provider_id=self.id)


@dataclass
class ChatCfg:
    provider_id: str = ""
    model: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    max_history_messages: int = 7


@dataclass
class EmbeddingCfg:
    provider_id: str = ""
    model: str = ""
    batch: int = 32


@dataclass
class RerankCfg:
    enabled: bool = False
    provider_id: str = ""
    model: str = ""


@dataclass
class VisionCfg:
    provider_id: str = ""
    model: str = ""


@dataclass
class AgentCfg:
    provider_id: str = ""
    model: str = ""
    max_iterations: int = 8
    system_prompt: str = ""
    tools: dict = field(default_factory=lambda: dict(DEFAULT_TOOLS))


@dataclass
class OcrCfg:
    enabled: bool = True  # 本地 OCR（扫描件识别），默认开启；与 AI 外呼无关


@dataclass
class McpServer:
    name: str
    url: str
    transport: str = "sse_legacy"  # streamable_http | sse_legacy（旧配置默认迁移为 sse_legacy）


@dataclass
class AiSettings:
    enabled: bool = False
    providers: list[Provider] = field(default_factory=list)
    chat: ChatCfg = field(default_factory=ChatCfg)
    embedding: EmbeddingCfg = field(default_factory=EmbeddingCfg)
    rerank: RerankCfg = field(default_factory=RerankCfg)
    vision: VisionCfg = field(default_factory=VisionCfg)
    agent: AgentCfg = field(default_factory=AgentCfg)
    ocr: OcrCfg = field(default_factory=OcrCfg)
    mcp_servers: list[McpServer] = field(default_factory=list)

    @property
    def ocr_enabled(self) -> bool:
        return bool(self.ocr.enabled)

    def provider(self, pid: str) -> Provider | None:
        return next((p for p in self.providers if p.id == pid), None)

    def vision_config(self) -> ProviderConfig | None:
        """图片理解 / 图片生成专用配置（独立于 Chat 模型）。"""
        p = self.provider(self.vision.provider_id)
        if not p or not p.base_url or not self.vision.model:
            return None
        return p.config(self.vision.model)

    def chat_config(self) -> ProviderConfig | None:
        p = self.provider(self.chat.provider_id)
        if not p or not p.base_url or not self.chat.model:
            return None
        return p.config(self.chat.model)

    def embedding_config(self) -> ProviderConfig | None:
        """已配置（不论是否启用）；用于状态展示与"待嵌入"判断。"""
        p = self.provider(self.embedding.provider_id)
        if not p or not p.base_url or not self.embedding.model:
            return None
        return p.config(self.embedding.model)

    def active_embedding_config(self) -> ProviderConfig | None:
        """全局外呼闸门：AI 关闭时返回 None，任何调用点不得发起嵌入请求。"""
        if not self.enabled:
            return None
        return self.embedding_config()

    def active_embedding_job(self) -> tuple[ProviderConfig, int] | None:
        """后台嵌入作业配置：(cfg, batch)；AI 关闭时返回 None（外呼闸门）。"""
        cfg = self.active_embedding_config()
        if cfg is None:
            return None
        return cfg, max(1, int(self.embedding.batch))

    def rerank_config(self) -> ProviderConfig | None:
        if not self.rerank.enabled:
            return None
        p = self.provider(self.rerank.provider_id)
        if not p or not p.base_url or not self.rerank.model:
            return None
        return p.config(self.rerank.model)

    def active_rerank_config(self) -> ProviderConfig | None:
        """全局外呼闸门：AI 关闭时返回 None，Rerank 不得外呼。"""
        if not self.enabled:
            return None
        return self.rerank_config()

    def agent_config(self) -> ProviderConfig | None:
        p = self.provider(self.agent.provider_id)
        if not p or not p.base_url or not self.agent.model:
            return None
        return p.config(self.agent.model)

    def agent_prompt(self) -> str:
        return self.agent.system_prompt.strip() or DEFAULT_AGENT_SYSTEM_PROMPT

    def tool_enabled(self, name: str) -> bool:
        return bool(self.agent.tools.get(name, DEFAULT_TOOLS.get(name, False)))


class AiStore:
    def __init__(self, path: Path, secrets: SecretStore | None = None):
        self.path = path
        self._secrets = secrets or SecretStore()
        self._lock = threading.RLock()

    # ---------- 读取 ----------

    def load(self) -> AiSettings:
        with self._lock:
            raw = self._normalize(self._read_raw())
            raw = self._migrate_plain_keys(raw)
            return self._to_settings(self._apply_env(raw))

    def _read_raw(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    # ---------- 保存 ----------

    def save(self, payload: dict) -> AiSettings:
        with self._lock:
            base = self._normalize(self._read_raw())
            merged = self._merge_payload(base, payload)
            self._atomic_write(merged)
        return self.load()

    def _atomic_write(self, merged: dict) -> None:
        """原子写 JSON；写前统一剥离 api_key，确保 Key 永不落盘。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_strip_keys(merged), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _set_secret(self, provider_id: str, key: str) -> None:
        """写系统凭据库；任何失败统一包装为 SecretStoreError（fail closed）。"""
        try:
            self._secrets.set(provider_id, key)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError(f"保存密钥失败：{exc}") from exc

    # ---------- 明文 Key 迁移 ----------

    def _migrate_plain_keys(self, raw: dict) -> dict:
        """旧版 JSON 中的明文 api_key → 系统凭据库。

        先逐条成功写入 SecretStore，全部成功后再原子重写 JSON 删除 Key；
        任一条失败则保持 JSON 原样（fail closed，不半迁移、不降级丢失）。
        """
        dirty = False
        for p in raw.get("providers", []):
            key = str(p.get("api_key") or "")
            if not key:
                continue
            try:
                self._secrets.set(str(p["id"]), key)
            except Exception as exc:
                logger = __import__("logging").getLogger(__name__)
                logger.warning("明文 Key 迁移失败（保留 JSON 原样）：%s", exc)
                return raw  # 不写 JSON，下次启动重试
            p["api_key"] = ""
            dirty = True
        if dirty:
            self._atomic_write(raw)
        return raw

    # ---------- 归一化 / 迁移 / 合并 ----------

    @staticmethod
    def _normalize(raw: dict) -> dict:
        """补齐默认结构；迁移 v1（chat/embedding 内联）→ v2。"""
        out = dict(raw)
        out["enabled"] = bool(raw.get("enabled", False))

        providers: list[dict] = []
        if isinstance(raw.get("providers"), list):
            for p in raw["providers"]:
                if isinstance(p, dict) and p.get("id"):
                    providers.append({
                        "id": str(p["id"]),
                        "name": str(p.get("name") or p["id"]),
                        "base_url": str(p.get("base_url") or ""),
                        "api_key": str(p.get("api_key") or ""),
                        "models": [str(m) for m in (p.get("models") or []) if m],
                    })

        chat_raw = raw.get("chat") or {}
        embedding_raw = raw.get("embedding") or {}

        # v1 迁移：chat/embedding 内联 base_url → 生成 provider（仅当存在内联地址时触发）
        if chat_raw.get("base_url") or embedding_raw.get("base_url"):
            def _ensure_provider(tag: str, cfg: dict) -> str:
                pid = tag
                if cfg.get("base_url"):
                    existing = next((p for p in providers if p["id"] == pid), None)
                    if existing is None:
                        providers.append({
                            "id": pid, "name": "默认" if pid == "default" else pid,
                            "base_url": str(cfg.get("base_url") or ""),
                            "api_key": str(cfg.get("api_key") or ""),
                            "models": [],
                        })
                    elif cfg.get("base_url") != existing["base_url"]:
                        providers.append({
                            "id": pid, "name": pid, "base_url": str(cfg.get("base_url") or ""),
                            "api_key": str(cfg.get("api_key") or ""), "models": [],
                        })
                return pid

            chat_pid = _ensure_provider("default", chat_raw)
            embed_base = embedding_raw.get("base_url") or ""
            embed_pid = chat_pid if (embed_base == chat_raw.get("base_url")) else "embed"
            if embed_base and embed_pid != chat_pid:
                _ensure_provider("embed", embedding_raw)
            out["chat"] = {
                "provider_id": chat_pid,
                "model": str(chat_raw.get("model") or ""),
                "temperature": float(chat_raw.get("temperature") or 0.3),
                "max_tokens": int(chat_raw.get("max_tokens") or 1024),
                "max_history_messages": int(chat_raw.get("max_history_messages") or 7),
            }
            out["embedding"] = {
                "provider_id": embed_pid,
                "model": str(embedding_raw.get("model") or ""),
                "batch": int(embedding_raw.get("batch") or 32),
            }

        out["providers"] = providers
        out["chat"] = _section(out.get("chat"), {
            "provider_id": "", "model": "", "temperature": 0.3, "max_tokens": 1024,
            "max_history_messages": 7,
        })
        out["embedding"] = _section(out.get("embedding"), {"provider_id": "", "model": "", "batch": 32})
        out["rerank"] = _section(out.get("rerank"), {
            "enabled": False, "provider_id": "", "model": "",
        })
        out["vision"] = _section(out.get("vision"), {"provider_id": "", "model": ""})
        out["agent"] = _section(out.get("agent"), {
            "provider_id": "", "model": "", "max_iterations": 8,
            "system_prompt": "", "tools": dict(DEFAULT_TOOLS),
        })
        out["ocr"] = _section(out.get("ocr"), {"enabled": True})
        out["mcp"] = _mcp_section(out.get("mcp"))
        return out

    def _merge_payload(self, base: dict, payload: dict) -> dict:
        """合并保存请求：providers 整表替换（api_key 为空时沿用旧值），其余按节合并。"""
        out = dict(base)
        if "enabled" in payload:
            out["enabled"] = bool(payload["enabled"])

        if isinstance(payload.get("providers"), list):
            old = {p["id"]: p for p in base.get("providers", [])}
            providers = []
            for p in payload["providers"]:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                pid = str(p["id"])
                prev = old.get(pid, {})
                key = str(p.get("api_key") or "")
                clear = bool(p.get("clear_api_key"))
                if clear:
                    # 显式清空：删除系统凭据；JSON 本就不含 Key
                    self._secrets.delete(pid)
                elif key:
                    # 提交新 Key：写入系统凭据库；失败抛错 → save 中止，JSON 未动
                    self._set_secret(pid, key)
                providers.append({
                    "id": pid,
                    "name": str(p.get("name") or pid),
                    "base_url": str(p.get("base_url") or ""),
                    # api_key 永不写入 JSON；不提交时沿用系统凭据库中的值
                    "models": [str(m) for m in (p.get("models") or []) if m],
                })
            # Provider 删除：同步清理系统凭据
            for pid in set(old) - {p["id"] for p in providers}:
                self._secrets.delete(pid)
            out["providers"] = providers

        for section in ("chat", "embedding", "rerank", "vision", "agent", "ocr"):
            if isinstance(payload.get(section), dict):
                out[section] = _merge_section(base.get(section, {}), payload[section], section)
        # 兼容 v1 保存格式：chat/embedding 内联 base_url → 迁移为 provider
        if isinstance(payload.get("chat"), dict) and payload["chat"].get("base_url"):
            _ensure_provider_in(out, "default", "默认", payload["chat"].get("base_url", ""), payload["chat"].get("api_key", ""), self._secrets)
            out["chat"] = _merge_section(base.get("chat", {}), {
                **{k: v for k, v in payload["chat"].items() if k not in ("base_url", "api_key")},
                "provider_id": "default",
            }, "chat")
        if isinstance(payload.get("embedding"), dict) and payload["embedding"].get("base_url"):
            _ensure_provider_in(out, "embed", "嵌入", payload["embedding"].get("base_url", ""), payload["embedding"].get("api_key", ""), self._secrets)
            out["embedding"] = _merge_section(base.get("embedding", {}), {
                **{k: v for k, v in payload["embedding"].items() if k not in ("base_url", "api_key")},
                "provider_id": "embed",
            }, "embedding")
        if isinstance(payload.get("mcp"), dict):
            out["mcp"] = _mcp_section(payload["mcp"])
        return out

    def _to_settings(self, raw: dict) -> AiSettings:
        providers = [
            Provider(
                id=p["id"], name=p["name"], base_url=p["base_url"],
                # Key 从系统凭据库注入（内存态），JSON 永不保存
                api_key=self._secrets.get(p["id"]), models=list(p["models"]),
            )
            for p in raw.get("providers", [])
        ]
        chat = raw["chat"]
        embedding = raw["embedding"]
        rerank = raw["rerank"]
        vision = raw.get("vision") or {}
        agent = raw["agent"]
        return AiSettings(
            enabled=bool(raw.get("enabled", False)),
            providers=providers,
            chat=ChatCfg(
                provider_id=chat["provider_id"], model=chat["model"],
                temperature=float(chat.get("temperature", 0.3)),
                max_tokens=int(chat.get("max_tokens", 1024)),
                max_history_messages=int(chat.get("max_history_messages", 7)),
            ),
            embedding=EmbeddingCfg(
                provider_id=embedding["provider_id"], model=embedding["model"],
                batch=int(embedding.get("batch", 32)),
            ),
            rerank=RerankCfg(
                enabled=bool(rerank.get("enabled", False)),
                provider_id=rerank.get("provider_id", ""),
                model=rerank.get("model", ""),
            ),
            vision=VisionCfg(
                provider_id=vision.get("provider_id", ""),
                model=vision.get("model", ""),
            ),
            agent=AgentCfg(
                provider_id=agent.get("provider_id", ""),
                model=agent.get("model", ""),
                max_iterations=int(agent.get("max_iterations", 8)),
                system_prompt=agent.get("system_prompt", ""),
                tools=dict(DEFAULT_TOOLS, **{k: bool(v) for k, v in (agent.get("tools") or {}).items()}),
            ),
            ocr=OcrCfg(enabled=bool((raw.get("ocr") or {}).get("enabled", True))),
            mcp_servers=[McpServer(
                name=s["name"], url=s["url"],
                transport=str(s.get("transport") or "sse_legacy"),
            ) for s in raw.get("mcp", {}).get("servers", [])],
        )

    def _apply_env(self, raw: dict) -> dict:
        """环境变量覆盖（云端 secret 注入，不落盘）。"""
        chat_env = {
            "base_url": os.environ.get("AI_CHAT_BASE_URL", "").strip(),
            "api_key": os.environ.get("AI_CHAT_API_KEY", "").strip(),
            "model": os.environ.get("AI_CHAT_MODEL", "").strip(),
        }
        embed_env = {
            "base_url": os.environ.get("AI_EMBED_BASE_URL", "").strip(),
            "api_key": os.environ.get("AI_EMBED_API_KEY", "").strip(),
            "model": os.environ.get("AI_EMBED_MODEL", "").strip(),
        }
        if any(chat_env.values()):
            providers = raw["providers"]
            prov = next((p for p in providers if p["id"] == "default"), None)
            if prov is None:
                prov = {"id": "default", "name": "环境变量", "base_url": "", "api_key": "", "models": []}
                providers.append(prov)
            if chat_env["base_url"]:
                prov["base_url"] = chat_env["base_url"]
            if chat_env["api_key"]:
                prov["api_key"] = chat_env["api_key"]
            raw["chat"]["provider_id"] = "default"
            if chat_env["model"]:
                raw["chat"]["model"] = chat_env["model"]
        if any(embed_env.values()):
            providers = raw["providers"]
            prov = next((p for p in providers if p["id"] == "embed"), None)
            if prov is None:
                prov = {"id": "embed", "name": "环境变量(嵌入)", "base_url": "", "api_key": "", "models": []}
                providers.append(prov)
            if embed_env["base_url"]:
                prov["base_url"] = embed_env["base_url"]
            if embed_env["api_key"]:
                prov["api_key"] = embed_env["api_key"]
            raw["embedding"]["provider_id"] = "embed"
            if embed_env["model"]:
                raw["embedding"]["model"] = embed_env["model"]
        return raw


def _section(raw, defaults: dict) -> dict:
    out = dict(defaults)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out:
                out[k] = v
    return out


def _ensure_provider_in(out: dict, pid: str, name: str, base_url: str, api_key: str, secrets=None) -> None:
    """按需补齐 provider（兼容 v1 保存格式）；同名不同 URL 时追加新条目。

    api_key 写入系统凭据库（secrets 提供时），不写入 JSON。
    """
    providers = out.get("providers", [])
    existing = next((p for p in providers if p["id"] == pid), None)
    if existing is None:
        providers.append({"id": pid, "name": name, "base_url": base_url, "models": []})
    elif base_url and base_url != existing["base_url"]:
        providers.append({"id": pid, "name": name, "base_url": base_url, "models": []})
    if api_key and secrets is not None:
        secrets.set(pid, api_key)
    out["providers"] = providers


def _merge_section(base: dict, payload: dict, section: str) -> dict:
    """逐键覆盖（允许清空字符串）；tools 做键级合并。"""
    out = dict(base)
    for k, v in payload.items():
        if k == "tools" and isinstance(v, dict):
            tools = dict(out.get("tools") or {})
            for tk, tv in v.items():
                tools[tk] = bool(tv)
            out["tools"] = tools
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = v
    return out


def _mcp_section(raw) -> dict:
    if not isinstance(raw, dict):
        return {"servers": []}
    servers = []
    for s in raw.get("servers") or []:
        if isinstance(s, dict) and s.get("name"):
            servers.append({
                "name": str(s["name"]),
                "url": str(s.get("url") or ""),
                # 旧配置无 transport → 迁移为 sse_legacy（保持兼容）
                "transport": str(s.get("transport") or "sse_legacy"),
            })
    return {"servers": servers}


def _strip_keys(raw: dict) -> dict:
    """写盘前剥离所有 api_key 字段（防御性：任何路径都不应落盘 Key）。"""
    out = dict(raw)
    for p in out.get("providers", []):
        if isinstance(p, dict):
            p.pop("api_key", None)
    return out


def new_provider_id() -> str:
    return "p" + uuid.uuid4().hex[:8]
