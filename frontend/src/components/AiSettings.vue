<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Database, Plug, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import IconButton from './IconButton.vue'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void; (e: 'saved'): void }>()

interface Status {
  enabled: boolean
  chat_configured: boolean
  embed_configured: boolean
  agent_configured: boolean
  providers: number
  chunks: number
  embedded: number
  has_vectors: boolean
  embedding_active: boolean
  worker_alive: boolean
  mcp_servers: number
}

interface Provider {
  id: string
  name: string
  base_url: string
  api_key?: string
  has_key?: boolean
  models: string[]
}

interface AiConfig {
  enabled: boolean
  providers: Provider[]
  chat: { provider_id: string; model: string; temperature: number; max_tokens: number; max_history_messages: number }
  embedding: { provider_id: string; model: string; batch: number }
  rerank: { enabled: boolean; provider_id: string; model: string }
  vision: { provider_id: string; model: string }
  agent: { provider_id: string; model: string; max_iterations: number; system_prompt: string; tools: Record<string, boolean> }
  ocr: { enabled: boolean }
  mcp: { servers: Array<{ name: string; url: string }> }
}

const TOOL_NAMES: Array<{ name: string; label: string; write: boolean }> = [
  { name: 'knowledge_search', label: '知识库检索', write: false },
  { name: 'read_note', label: '读取笔记', write: false },
  { name: 'list_notes', label: '列出笔记', write: false },
  { name: 'sql', label: 'SQL 查询（只读）', write: false },
  { name: 'create_note', label: '新建笔记（需确认）', write: true },
  { name: 'update_note', label: '更新笔记（需确认）', write: true },
  { name: 'image.analyze', label: '图片理解', write: false },
  { name: 'image.generate', label: '图片生成（需确认）', write: true },
]

const status = ref<Status | null>(null)
const cfg = ref<AiConfig | null>(null)
const testing = ref(false)
const rebuilding = ref(false)
const listingModels = ref('')
const saving = ref(false)

async function loadStatus(): Promise<void> {
  try {
    status.value = await api.aiStatus()
  } catch {
    status.value = null
  }
}

async function loadConfig(): Promise<void> {
  try {
    cfg.value = await api.aiConfig()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

function applyStatus(s: Partial<Status> & { enabled: boolean }): void {
  if (status.value) {
    status.value = { ...status.value, ...s }
  } else {
    status.value = {
      enabled: s.enabled, chat_configured: false, embed_configured: false,
      agent_configured: false, providers: 0, chunks: 0, embedded: 0,
      has_vectors: false, embedding_active: false, worker_alive: false, mcp_servers: 0,
    }
  }
}

function providerOptions(): Array<{ id: string; name: string }> {
  return (cfg.value?.providers ?? []).map((p) => ({ id: p.id, name: p.name || p.id }))
}

function addProvider(): void {
  cfg.value?.providers.push({ id: `p${Date.now().toString(16)}`, name: '', base_url: '', models: [] })
}

function removeProvider(id: string): void {
  if (!cfg.value) return
  cfg.value.providers = cfg.value.providers.filter((p) => p.id !== id)
  if (cfg.value.chat.provider_id === id) { cfg.value.chat.provider_id = ''; cfg.value.chat.model = '' }
  if (cfg.value.embedding.provider_id === id) { cfg.value.embedding.provider_id = ''; cfg.value.embedding.model = '' }
  if (cfg.value.rerank.provider_id === id) { cfg.value.rerank.provider_id = ''; cfg.value.rerank.model = '' }
  if (cfg.value.agent.provider_id === id) { cfg.value.agent.provider_id = ''; cfg.value.agent.model = '' }
}

async function listModels(providerId: string): Promise<void> {
  listingModels.value = providerId
  try {
    const r = await api.aiListModels(providerId)
    const p = cfg.value?.providers.find((x) => x.id === providerId)
    if (p && r.ok) {
      p.models = r.models
      emit('notify', `拉取到 ${r.models.length} 个模型`, 'info')
    } else if (!r.ok) {
      emit('notify', r.message || '拉取模型列表失败', 'error')
    }
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  } finally {
    listingModels.value = ''
  }
}

function buildPayload(): Record<string, unknown> {
  if (!cfg.value) return {}
  return {
    enabled: cfg.value.enabled,
    providers: cfg.value.providers.map((p) => ({
      id: p.id,
      name: p.name || p.id,
      base_url: p.base_url,
      ...(p.api_key ? { api_key: p.api_key } : {}),
      models: p.models ?? [],
    })),
    chat: { ...cfg.value.chat },
    embedding: { ...cfg.value.embedding },
    rerank: { ...cfg.value.rerank },
    vision: { ...cfg.value.vision },
    ocr: { ...cfg.value.ocr },
    agent: { ...cfg.value.agent },
    mcp: cfg.value.mcp,
  }
}

async function saveConfig(): Promise<void> {
  if (!cfg.value) return
  saving.value = true
  try {
    const s = await api.aiSaveConfig(buildPayload())
    applyStatus(s)
    emit('notify', 'AI 配置已保存', 'info')
    if (s.embedding_changed) {
      emit('notify', 'Embedding 模型已变更，后台重新嵌入…', 'info')
    }
    emit('saved')
    await loadConfig()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  } finally {
    saving.value = false
  }
}

async function test(): Promise<void> {
  testing.value = true
  try {
    const r = await api.aiTest()
    emit('notify', r.message, r.ok ? 'info' : 'error')
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  } finally {
    testing.value = false
  }
}

async function rebuild(): Promise<void> {
  rebuilding.value = true
  try {
    const r = await api.aiRebuild()
    emit('notify', `AI 索引完成：${r.reindexed} 个文件重新分块，${r.pending} 个片段待嵌入`, 'info')
    await loadStatus()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  } finally {
    rebuilding.value = false
  }
}

onMounted(() => {
  void loadStatus()
  void loadConfig()
})
</script>

<template>
  <div class="ai-settings">
    <div class="ai-settings-head">
      <h2>AI 设置</h2>
      <span class="panel-header-count">{{ status?.enabled ? '已启用' : '未启用' }}</span>
    </div>

    <label v-if="cfg" class="ai-toggle ai-section">
      <input v-model="cfg.enabled" type="checkbox" />
      启用 AI
    </label>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">模型服务（Providers）</div>
      <div v-for="p in cfg.providers" :key="p.id" class="ai-provider">
        <div class="ai-provider-row">
          <input v-model="p.name" class="ai-provider-name" placeholder="名称" />
          <IconButton
            title="拉取模型列表"
            :disabled="listingModels === p.id"
            @click="listModels(p.id)"
          >
            <RefreshCw :size="13" :class="{ spin: listingModels === p.id }" />
          </IconButton>
          <IconButton title="删除" kind="danger" @click="removeProvider(p.id)"><Trash2 :size="13" /></IconButton>
        </div>
        <div class="ai-field"><label>Base URL</label><input v-model="p.base_url" placeholder="https://api.openai.com/v1" /></div>
        <div class="ai-field">
          <label>Key</label>
          <input v-model="p.api_key" type="password" :placeholder="p.has_key ? '已设置（留空不变）' : 'sk-…'" autocomplete="off" />
        </div>
        <div v-if="p.models.length" class="ai-field">
          <label>模型</label>
          <select v-model="cfg.chat.model" class="ai-select">
            <option v-for="m in p.models" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
      </div>
      <button class="btn" @click="addProvider"><Plus :size="13" /> 添加 Provider</button>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">Chat 配置</div>
      <div class="ai-field">
        <label>Provider</label>
        <select v-model="cfg.chat.provider_id" class="ai-select">
          <option value="">—</option>
          <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="ai-field"><label>模型</label><input v-model="cfg.chat.model" placeholder="gpt-4o-mini / deepseek-chat" /></div>
      <div class="ai-field"><label>温度</label><input v-model.number="cfg.chat.temperature" type="number" step="0.1" min="0" max="2" /></div>
      <div class="ai-field"><label>历史轮数</label><input v-model.number="cfg.chat.max_history_messages" type="number" min="1" max="50" /></div>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">Embedding 配置</div>
      <div class="ai-field">
        <label>Provider</label>
        <select v-model="cfg.embedding.provider_id" class="ai-select">
          <option value="">—</option>
          <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="ai-field"><label>模型</label><input v-model="cfg.embedding.model" placeholder="text-embedding-3-small" /></div>
      <div class="ai-field"><label>批量</label><input v-model.number="cfg.embedding.batch" type="number" min="1" max="256" /></div>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">Rerank 精排</div>
      <label class="ai-toggle"><input v-model="cfg.rerank.enabled" type="checkbox" /> 启用 Rerank</label>
      <div class="ai-field">
        <label>Provider</label>
        <select v-model="cfg.rerank.provider_id" class="ai-select">
          <option value="">—</option>
          <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="ai-field"><label>模型</label><input v-model="cfg.rerank.model" placeholder="jina-reranker-v2" /></div>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">Vision 配置（图片理解 / 生成）</div>
      <div class="ai-field">
        <label>Provider</label>
        <select v-model="cfg.vision.provider_id" class="ai-select">
          <option value="">—</option>
          <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="ai-field"><label>模型</label><input v-model="cfg.vision.model" placeholder="gpt-4o / gemini-1.5-pro" /></div>
      <p class="ai-note">独立于 Chat 模型；未配置时图片理解/生成回退使用 Agent 模型。</p>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">本地 OCR（扫描件识别）</div>
      <label class="ai-toggle"><input v-model="cfg.ocr.enabled" type="checkbox" /> 启用 OCR 识别</label>
      <p class="ai-note">扫描版 PDF（无文字层）导入后由后台任务本地识别文字，进入知识库索引与预览；不调用外部服务。</p>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">Agent 配置（function calling）</div>
      <div class="ai-field">
        <label>Provider</label>
        <select v-model="cfg.agent.provider_id" class="ai-select">
          <option value="">—</option>
          <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="ai-field"><label>模型</label><input v-model="cfg.agent.model" placeholder="需支持 tools 的模型" /></div>
      <div class="ai-field"><label>最大轮数</label><input v-model.number="cfg.agent.max_iterations" type="number" min="1" max="32" /></div>
      <textarea v-model="cfg.agent.system_prompt" class="ai-textarea" rows="4" placeholder="自定义系统提示词（留空使用默认）" />
      <div class="ai-tools">
        <label v-for="t in TOOL_NAMES" :key="t.name" class="ai-tool-check">
          <input v-model="cfg.agent.tools[t.name]" type="checkbox" />
          {{ t.label }}
        </label>
      </div>
    </div>

    <div v-if="cfg" class="ai-section">
      <div class="ai-section-title">MCP 服务器</div>
      <div v-for="(s, i) in cfg.mcp.servers" :key="i" class="ai-mcp-row">
        <input v-model="s.name" placeholder="名称" />
        <input v-model="s.url" placeholder="https://mcp.example.com/sse" />
        <IconButton title="删除" kind="danger" @click="cfg.mcp.servers.splice(i, 1)"><Trash2 :size="13" /></IconButton>
      </div>
      <button class="btn" @click="cfg.mcp.servers.push({ name: '', url: '' })"><Plus :size="13" /> 添加 MCP Server</button>
    </div>

    <div v-if="status" class="ai-section">
      <div class="ai-section-title">索引状态</div>
      <p class="ai-index-stat">
        <Database :size="13" />
        {{ status.chunks }} 个片段 / 已嵌入 {{ status.embedded }}
        <span v-if="status.embedding_active && (status.worker_alive || status.chunks > status.embedded)" class="ai-status-busy">（后台嵌入中…）</span>
      </p>
      <button class="btn" :disabled="rebuilding" @click="rebuild">
        <RefreshCw :size="13" :class="{ spin: rebuilding }" /> {{ rebuilding ? '索引中…' : '重建 AI 索引' }}
      </button>
    </div>

    <div class="ai-settings-actions">
      <button class="btn btn--primary" :disabled="saving || !cfg" @click="saveConfig">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <button class="btn" :disabled="testing" @click="test"><Plug :size="13" /> {{ testing ? '测试中…' : '测试连接' }}</button>
    </div>
    <p class="ai-note">
      开启后匹配的笔记片段将发送给所选模型服务商；本地 Ollama 数据不出本机。
      Agent 写操作（新建/更新笔记、生成图片）会先请求确认。
    </p>
  </div>
</template>
