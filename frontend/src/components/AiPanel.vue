<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { History, RefreshCw, Send, Settings2, Square, Plus, Trash2 } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'
import AiSettings from './AiSettings.vue'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Status {
  enabled: boolean
  chat_configured: boolean
  embed_configured: boolean
  embedding_active: boolean
  agent_configured: boolean
  providers: number
  chunks: number
  embedded: number
  has_vectors: boolean
  worker_alive: boolean
  mcp_servers: number
}

interface Source {
  index: number
  path: string
  heading: string
  line_start?: number
  line_end?: number
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources: Source[]
}

const status = ref<Status | null>(null)
const showSettings = ref(false)
const mode = ref<'rag' | 'free'>('rag')
const sessionId = ref<string | null>(null)

const input = ref('')
const messages = ref<ChatMessage[]>([])
const busy = ref(false)
let controller: AbortController | null = null
let statTimer: number | undefined

async function loadStatus(): Promise<void> {
  try {
    status.value = await api.aiStatus()
  } catch {
    status.value = null
  }
}

function onAiOpen(): void {
  void loadStatus()
}

function pollStat(): void {
  const st = status.value
  // 仅在嵌入真正 active（启用且已配置）时轮询；调度器存活 ≠ 嵌入中
  if (!st || !st.embedding_active || (!st.worker_alive && !(st.chunks > st.embedded))) return
  void api.aiEmbeddingStat().then((s) => {
    if (status.value) {
      status.value.worker_alive = s.running
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
    showSessions.value = false
    await loadFreeSessions()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

interface FreeSession {
  id: string
  title: string
  updated_at: string
}
const freeSessions = ref<FreeSession[]>([])
const showSessions = ref(false)

async function loadFreeSessions(): Promise<void> {
  try {
    const r = await api.aiChatSessions()
    freeSessions.value = r.sessions
  } catch {
    /* ignore */
  }
}

async function openFreeSession(id: string): Promise<void> {
  try {
    const r = await api.aiChatGetSession(id)
    if (!r.session) {
      emit('notify', '会话不存在', 'error')
      return
    }
    sessionId.value = id
    messages.value = (r.session.messages ?? []).map((raw) => {
      const m = raw as { role?: string; content?: string }
      return { role: m.role === 'user' ? 'user' : 'assistant', content: m.content ?? '', sources: [] }
    })
    showSessions.value = false
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function deleteFreeSession(id: string): Promise<void> {
  try {
    await api.aiClearSession(id)
    if (sessionId.value === id) {
      sessionId.value = null
      messages.value = []
    }
    await loadFreeSessions()
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
          if (payload.type === 'session') {
            sessionId.value = payload.session_id
          } else if (payload.type === 'delta') {
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

/** 来源显示：标题 · 章节 · 行号 */
function sourceLabel(s: Source): { title: string; meta: string } {
  const seg = s.path.split('/')
  const title = seg[seg.length - 1] || s.path
  const heading = s.heading ? ` · ${s.heading}` : ''
  const line = s.line_start ? ` · 行 ${s.line_start}${s.line_end && s.line_end > s.line_start ? `-${s.line_end}` : ''}` : ''
  return { title, meta: `${heading}${line}` }
}

function jumpSource(s: Source): void {
  // 非 Markdown 文档走只读预览标签；Markdown 正常打开
  void openTab(s.path).catch((e) => emit('notify', (e as Error).message, 'error'))
}

function onSettingsSaved(): void {
  void loadStatus()
}

onMounted(() => {
  void loadStatus()
  void loadFreeSessions()
  window.addEventListener('ai-open', onAiOpen)
  statTimer = window.setInterval(pollStat, 3000)
})

onBeforeUnmount(() => {
  controller?.abort()
  window.clearInterval(statTimer)
  window.removeEventListener('ai-open', onAiOpen)
})
</script>

<template>
  <div class="ai-panel">
    <div class="panel-header">
      <span class="panel-header-title">AI 问答</span>
      <span v-if="status" class="panel-header-count">{{ status.enabled ? '已启用' : '未启用' }}</span>
      <div class="panel-header-actions">
        <IconButton title="AI 设置" @click="showSettings = !showSettings"><Settings2 :size="14" /></IconButton>
      </div>
    </div>

    <template v-if="showSettings">
      <AiSettings @notify="(m, k) => emit('notify', m, k)" @saved="onSettingsSaved" />
    </template>

    <template v-else>
      <!-- 模式切换 -->
      <div class="ai-mode-row">
        <div class="seg ai-mode-seg">
          <button :class="{ on: mode === 'rag' }" @click="mode = 'rag'">知识库问答</button>
          <button :class="{ on: mode === 'free' }" @click="mode = 'free'">自由对话</button>
        </div>
        <template v-if="mode === 'free'">
          <IconButton title="会话历史" @click="showSessions = !showSessions"><History :size="13" /></IconButton>
          <IconButton title="新会话" @click="newFreeSession"><Plus :size="13" /></IconButton>
        </template>
      </div>

      <!-- 自由对话会话列表 -->
      <div v-if="showSessions" class="agent-sessions">
        <div v-for="s in freeSessions" :key="s.id" class="agent-session-row">
          <button class="agent-session-name" :class="{ on: s.id === sessionId }" @click="openFreeSession(s.id)">
            {{ s.title }}
          </button>
          <IconButton title="删除" kind="danger" @click="deleteFreeSession(s.id)"><Trash2 :size="12" /></IconButton>
        </div>
        <div v-if="!freeSessions.length" class="panel-empty">暂无会话</div>
      </div>

      <!-- 状态行 -->
      <div v-if="status" class="ai-statusline">
        <span v-if="status.enabled">
          {{ status.chunks }} 块 / 已嵌入 {{ status.embedded }}
        </span>
        <span v-else>AI 未启用，请在设置中配置</span>
        <span v-if="status.embedding_active && (status.worker_alive || status.chunks > status.embedded)" class="ai-status-busy">
          <RefreshCw :size="11" class="spin" /> 后台嵌入中
        </span>
      </div>

      <!-- 研究边注消息流 -->
      <div class="ai-messages">
        <EmptyState v-if="messages.length === 0">
          <template #icon><History :size="22" /></template>
          {{ mode === 'rag' ? '输入问题，AI 基于知识库回答并标注来源（标题 · 章节 · 行号）' : '输入问题与 AI 自由对话（可跨轮记忆）' }}
        </EmptyState>
        <div v-for="(m, i) in messages" :key="i" class="ai-msg" :class="m.role">
          <div class="ai-msg-role">{{ m.role === 'user' ? '你' : mode === 'rag' ? 'AI · 知识库问答' : 'AI · 自由对话' }}</div>
          <div class="ai-msg-content">{{ m.content || '…' }}</div>
          <div v-if="m.role === 'assistant' && m.sources.length" class="ai-sources">
            <span class="ai-sources-label">来源</span>
            <button v-for="s in m.sources" :key="s.index" class="ai-source" @click="jumpSource(s)">
              <span class="ai-source-path">[{{ s.index }}] {{ sourceLabel(s).title }}</span>
              <span class="ai-source-meta">{{ sourceLabel(s).meta }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 输入行 -->
      <div class="ai-input-row">
        <input
          v-model="input"
          :placeholder="mode === 'rag' ? '基于知识库提问…' : '自由对话…'"
          :disabled="busy"
          @keyup.enter="send"
        />
        <IconButton v-if="busy" title="停止" @click="stop"><Square :size="14" /></IconButton>
        <IconButton v-else title="发送" @click="send"><Send :size="14" /></IconButton>
        <IconButton v-if="messages.length" title="重试上一个问题" @click="retry"><RefreshCw :size="14" /></IconButton>
      </div>
    </template>
  </div>
</template>
