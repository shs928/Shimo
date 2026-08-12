<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Send, Square, RefreshCw, Plug, Database, Settings2, Plus, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Status {
  enabled: boolean
  chat_configured: boolean
  embed_configured: boolean
  agent_configured: boolean
  providers: number
  chunks: number
  embedded: number
  has_vectors: boolean
  embedding_running: boolean
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
  agent: { provider_id: string; model: string; max_iterations: number; system_prompt: string; tools: Record<string, boolean> }
  mcp: { servers: Array<{ name: string; url: string }> }
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources: Array<{ index: number; path: string; heading: string; line_start?: number; line_end?: number }>
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
const showConfig = ref(false)
const mode = ref<'rag' | 'free'>('rag')
const cfg = ref<AiConfig | null>(null)
const sessionId = ref<string | null>(null)
const testing = ref(false)
const rebuilding = ref(false)
const listingModels = ref('')

const input = ref('')
const messages = ref<ChatMessage[]>([])
const busy = ref(false)
let controller: AbortController | null = null
let statTimer: number | undefined
let configLoaded = false

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
    configLoaded = true
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
      has_vectors: false, embedding_running: false, mcp_servers: 0,
    }
  }
}

function providerOptions(): Array<{ id: string; name: string }> {
  return (cfg.value?.providers ?? []).map((p) => ({ id: p.id, name: p.name || p.id }))
}

function providerName(id: string): string {
  return cfg.value?.providers.find((p) => p.id === id)?.name ?? id
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
    agent: { ...cfg.value.agent },
    mcp: cfg.value.mcp,
  }
}

async function saveConfig(): Promise<void> {
  try {
    const s = await api.aiSaveConfig(buildPayload())
    applyStatus(s)
    emit('notify', 'AI 配置已保存', 'info')
    await loadConfig()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
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

function pollStat(): void {
  const st = status.value
  if (!st || (!st.embedding_running && !(st.chunks > st.embedded))) return
  void api.aiEmbeddingStat().then((s) => {
    if (status.value) {
      status.value.embedding_running = s.running
      status.value.embedded = s.embedded
      status.value.chunks = s.total
    }
  }).catch(() => undefined)
}

async function newFreeSession(): Promise<void> {
  try {
    const r = await api.aiNewSession()
    sessionId.value = r.session_id
    messages.value = []
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: text, sources: [] })
  messages.value.push({ role: 'assistant', content: '', sources: [] })
  busy.value = true
  controller = new AbortController()

  try {
    const resp = await api.aiChat(text, mode.value, mode.value === 'free' ? sessionId.value ?? undefined : undefined, controller.signal)
    if (!resp.ok || !resp.body) {
      const detail = await resp.text()
      emit('notify', `AI 请求失败：${detail.slice(0, 120)}`, 'error')
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let done = false

    while (!done) {
      const { value, done: d } = await reader.read()
      done = d
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''
      for (const ev of events) {
        const line = ev.trim()
        if (!line.startsWith('data:')) continue
        try {
          const payload = JSON.parse(line.slice(5).trim())
          if (payload.type === 'delta') {
            const assistant = messages.value[messages.value.length - 1]
            assistant.content += payload.content
          } else if (payload.type === 'done') {
            const assistant = messages.value[messages.value.length - 1]
            assistant.sources = payload.sources ?? []
          } else if (payload.type === 'error') {
            const assistant = messages.value[messages.value.length - 1]
            assistant.content = `⚠️ ${payload.error}`
          }
        } catch {
          /* 忽略不完整事件 */
        }
      }
    }
  } catch (e) {
    const err = e as Error
    if (err.name !== 'AbortError') {
      const assistant = messages.value[messages.value.length - 1]
      assistant.content = `⚠️ ${err.message}`
    }
  } finally {
    busy.value = false
    controller = null
  }
}

function stop(): void {
  controller?.abort()
}

function retry(): void {
  const last = [...messages.value].reverse().find((m) => m.role === 'user')
  if (last) {
    messages.value.pop()
    messages.value.pop()
    input.value = last.content
  }
}

function jumpSource(path: string): void {
  // 非 Markdown 文档（PDF/Word 等）通过 raw 端点预览，Markdown 打开为标签
  if (!path.toLowerCase().endsWith('.md')) {
    window.open(`/api/v1/raw/${encodeURIComponent(path)}`, '_blank', 'noopener')
    return
  }
  void openTab(path).catch((e) => emit('notify', (e as Error).message, 'error'))
}

function toggleConfig(): void {
  showConfig.value = !showConfig.value
  if (showConfig.value && !configLoaded) void loadConfig()
}

onMounted(() => {
  void loadStatus()
  window.addEventListener('ai-open', () => void loadStatus())
  statTimer = window.setInterval(pollStat, 3000)
})

onBeforeUnmount(() => {
  controller?.abort()
  window.clearInterval(statTimer)
  window.removeEventListener('ai-open', () => void loadStatus())
})
</script>

<template>
  <div class="ai-panel">
    <div class="dock-title">
      <span>AI 问答</span>
      <span v-if="status" class="dock-count">{{ status.enabled ? '已启用' : '未启用' }}</span>
      <button class="icon-btn ai-config-btn" title="配置" @click="toggleConfig">
        <Settings2 :size="14" />
      </button>
    </div>

    <!-- 模式切换 -->
    <div v-if="!showConfig" class="ai-mode-row">
      <div class="seg ai-mode-seg">
        <button :class="{ on: mode === 'rag' }" @click="mode = 'rag'">知识库问答</button>
        <button :class="{ on: mode === 'free' }" @click="mode = 'free'">自由对话</button>
      </div>
      <button v-if="mode === 'free'" class="icon-btn" title="新会话" @click="newFreeSession">
        <Plus :size="13" />
      </button>
    </div>

    <div v-if="showConfig" class="ai-config">
      <label class="ai-toggle">
        <input v-model="cfg!.enabled" type="checkbox" />
        启用 AI
      </label>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">模型服务（Providers）</div>
        <div v-for="p in cfg.providers" :key="p.id" class="ai-provider">
          <div class="ai-provider-row">
            <input v-model="p.name" class="ai-provider-name" placeholder="名称" />
            <button class="icon-btn" title="拉取模型列表" :disabled="listingModels === p.id" @click="listModels(p.id)">
              <RefreshCw :size="12" :class="{ spin: listingModels === p.id }" />
            </button>
            <button class="icon-btn danger" title="删除" @click="removeProvider(p.id)"><Trash2 :size="12" /></button>
          </div>
          <div class="ai-field"><span>Base URL</span><input v-model="p.base_url" placeholder="https://api.openai.com/v1" /></div>
          <div class="ai-field"><span>Key</span><input v-model="p.api_key" type="password" :placeholder="p.has_key ? '已设置（留空不变）' : 'sk-…'" /></div>
          <div v-if="p.models.length" class="ai-field"><span>模型</span><select v-model="cfg.chat.model" class="ai-select">
            <option v-for="m in p.models" :key="m" :value="m">{{ m }}</option>
          </select></div>
        </div>
        <button class="ai-add-provider" @click="addProvider"><Plus :size="12" /> 添加 Provider</button>
      </div>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">Chat 配置</div>
        <div class="ai-field">
          <span>Provider</span>
          <select v-model="cfg.chat.provider_id" class="ai-select">
            <option value="">—</option>
            <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </div>
        <div class="ai-field"><span>模型</span><input v-model="cfg.chat.model" placeholder="gpt-4o-mini / deepseek-chat" /></div>
        <div class="ai-field"><span>温度</span><input v-model.number="cfg.chat.temperature" type="number" step="0.1" min="0" max="2" /></div>
        <div class="ai-field"><span>历史轮数</span><input v-model.number="cfg.chat.max_history_messages" type="number" min="1" max="50" /></div>
      </div>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">Embedding 配置</div>
        <div class="ai-field">
          <span>Provider</span>
          <select v-model="cfg.embedding.provider_id" class="ai-select">
            <option value="">—</option>
            <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </div>
        <div class="ai-field"><span>模型</span><input v-model="cfg.embedding.model" placeholder="text-embedding-3-small" /></div>
        <div class="ai-field"><span>批量</span><input v-model.number="cfg.embedding.batch" type="number" min="1" max="256" /></div>
      </div>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">Rerank 精排</div>
        <label class="ai-toggle"><input v-model="cfg.rerank.enabled" type="checkbox" /> 启用 Rerank</label>
        <div class="ai-field">
          <span>Provider</span>
          <select v-model="cfg.rerank.provider_id" class="ai-select">
            <option value="">—</option>
            <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </div>
        <div class="ai-field"><span>模型</span><input v-model="cfg.rerank.model" placeholder="jina-reranker-v2" /></div>
      </div>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">Agent 配置（function calling）</div>
        <div class="ai-field">
          <span>Provider</span>
          <select v-model="cfg.agent.provider_id" class="ai-select">
            <option value="">—</option>
            <option v-for="p in providerOptions()" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </div>
        <div class="ai-field"><span>模型</span><input v-model="cfg.agent.model" placeholder="需支持 tools 的模型" /></div>
        <div class="ai-field"><span>最大轮数</span><input v-model.number="cfg.agent.max_iterations" type="number" min="1" max="32" /></div>
        <textarea v-model="cfg.agent.system_prompt" class="ai-textarea" rows="4" placeholder="自定义系统提示词（留空使用默认）" />
        <div class="ai-tools">
          <label v-for="t in TOOL_NAMES" :key="t.name" class="ai-tool-check">
            <input v-model="cfg.agent.tools[t.name]" type="checkbox" />
            {{ t.label }}
          </label>
        </div>
      </div>

      <div v-if="cfg" class="ai-config-section">
        <div class="ai-section-title">MCP 服务器</div>
        <div v-for="(s, i) in cfg.mcp.servers" :key="i" class="ai-mcp-row">
          <input v-model="s.name" placeholder="名称" />
          <input v-model="s.url" placeholder="https://mcp.example.com/sse" />
          <button class="icon-btn danger" title="删除" @click="cfg.mcp.servers.splice(i, 1)"><Trash2 :size="12" /></button>
        </div>
        <button class="ai-add-provider" @click="cfg.mcp.servers.push({ name: '', url: '' })"><Plus :size="12" /> 添加 MCP Server</button>
      </div>

      <div v-if="status" class="ai-config-section">
        <div class="ai-section-title">索引状态</div>
        <p class="ai-note">
          {{ status.chunks }} 个片段 / 已嵌入 {{ status.embedded }}
          <span v-if="status.embedding_running || status.chunks > status.embedded">（后台嵌入中…）</span>
        </p>
        <div class="ai-config-actions">
          <button :disabled="rebuilding" @click="rebuild">
            <Database :size="12" /> {{ rebuilding ? '索引中…' : '重建索引' }}
          </button>
        </div>
      </div>

      <div class="ai-config-actions">
        <button @click="saveConfig">保存</button>
        <button :disabled="testing" @click="test"><Plug :size="12" /> {{ testing ? '测试中…' : '测试连接' }}</button>
      </div>
      <p class="ai-note">开启后匹配的笔记片段将发送给所选模型服务商；本地 Ollama 数据不出本机。Agent 写操作（新建/更新笔记、生成图片）会先请求确认。</p>
    </div>

    <div v-else-if="status" class="ai-status">
      <button class="ai-rebuild" :disabled="rebuilding" @click="rebuild">
        <Database :size="12" /> {{ rebuilding ? '索引中…' : `重建 AI 索引（${status.chunks} 块 / 已嵌入 ${status.embedded}）` }}
      </button>
    </div>

    <div class="ai-messages">
      <div v-if="messages.length === 0" class="panel-empty">
        {{ mode === 'rag' ? '输入问题，AI 会基于知识库内容回答并给出来源' : '输入问题与 AI 自由对话（可跨轮记忆）' }}
      </div>
      <div v-for="(m, i) in messages" :key="i" class="ai-msg" :class="m.role">
        <div class="ai-msg-role">{{ m.role === 'user' ? '我' : 'AI' }}</div>
        <div class="ai-msg-content">{{ m.content || '…' }}</div>
        <div v-if="m.role === 'assistant' && m.sources.length" class="ai-sources">
          <span class="ai-sources-label">来源：</span>
          <button v-for="s in m.sources" :key="s.index" class="ai-source" @click="jumpSource(s.path)">
            [{{ s.index }}] {{ s.path }}
          </button>
        </div>
      </div>
    </div>

    <div class="ai-input-row">
      <input
        v-model="input"
        :placeholder="mode === 'rag' ? '基于知识库提问…' : '自由对话…'"
        :disabled="busy"
        @keyup.enter="send"
      />
      <button v-if="busy" class="icon-btn" title="停止" @click="stop"><Square :size="14" /></button>
      <button v-else class="icon-btn" title="发送" @click="send"><Send :size="14" /></button>
      <button v-if="messages.length" class="icon-btn" title="重试上一个问题" @click="retry"><RefreshCw :size="14" /></button>
    </div>
  </div>
</template>
