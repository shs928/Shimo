<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { AlertTriangle, History, Plus, RefreshCw, Send, Square, Trash2, Wrench } from 'lucide-vue-next'
import { api } from '../api'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface ToolCall {
  tool: string
  args: Record<string, unknown>
  status?: string
  result?: string
}

interface ConfirmItem {
  request_id: string
  tool: string
  summary: string
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  tools: ToolCall[]
  confirms: ConfirmItem[]
  error?: string
}

interface SessionInfo {
  id: string
  title: string
  updated_at: string
}

/** 工具状态本地化 */
const STATUS_LABEL: Record<string, string> = {
  pending: '执行中',
  ok: '完成',
  error: '失败',
  denied: '已拒绝',
}

const sessions = ref<SessionInfo[]>([])
const activeId = ref('')
const messages = ref<Msg[]>([])
const input = ref('')
const busy = ref(false)
const showSessions = ref(false)
const currentSessionTitle = ref('新会话')
let controller: AbortController | null = null

function argPreview(args: Record<string, unknown>): string {
  const s = JSON.stringify(args) ?? ''
  return s.length > 120 ? s.slice(0, 120) + '…' : s
}

async function loadSessions(): Promise<void> {
  try {
    const r = await api.aiAgentSessions()
    sessions.value = r.sessions
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

/** 新会话：真正调用后端创建 */
async function newSession(): Promise<void> {
  try {
    const r = await api.aiAgentCreateSession()
    activeId.value = r.session_id
    messages.value = []
    currentSessionTitle.value = '新会话'
    showSessions.value = false
    await loadSessions()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

function clearLocal(): void {
  activeId.value = ''
  messages.value = []
  currentSessionTitle.value = '新会话'
  showSessions.value = false
}

async function openSession(id: string): Promise<void> {
  try {
    const r = await api.aiAgentSession(id)
    if (!r.session) {
      emit('notify', '会话不存在', 'error')
      return
    }
    activeId.value = id
    currentSessionTitle.value = r.session.title || '新会话'
    messages.value = (r.session.messages ?? []).map((raw) => {
      const m = raw as { role?: string; content?: string }
      return {
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content ?? '',
        tools: [],
        confirms: [],
      }
    })
    showSessions.value = false
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function deleteSession(id: string): Promise<void> {
  try {
    await api.aiAgentDeleteSession(id)
    if (activeId.value === id) clearLocal()
    await loadSessions()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: text, tools: [], confirms: [] })
  const assistant: Msg = { role: 'assistant', content: '', tools: [], confirms: [] }
  messages.value.push(assistant)
  busy.value = true
  controller = new AbortController()

  try {
    const resp = await api.aiAgentChat(text, activeId.value || undefined, controller.signal)
    if (!resp.ok || !resp.body) {
      assistant.error = (await resp.text()).slice(0, 160) || '请求失败'
      return
    }
    const body = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await body.read()
      if (done) break
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''
      for (const ev of events) {
        const line = ev.trim()
        if (!line.startsWith('data:')) continue
        let payload: Record<string, unknown>
        try {
          payload = JSON.parse(line.slice(5).trim())
        } catch {
          continue
        }
        handleEvent(payload, assistant)
      }
    }
  } catch (e) {
    const err = e as Error
    if (err.name !== 'AbortError') assistant.error = err.message
  } finally {
    busy.value = false
    controller = null
    void loadSessions()
    if (activeId.value) {
      try {
        const r = await api.aiAgentSession(activeId.value)
        if (r.session?.title && r.session.title !== '新会话') currentSessionTitle.value = r.session.title
      } catch {
        /* ignore */
      }
    }
  }
}

function handleEvent(payload: Record<string, unknown>, assistant: Msg): void {
  switch (payload.type) {
    case 'delta':
      assistant.content += String(payload.content ?? '')
      break
    case 'tool_call':
      assistant.tools.push({
        tool: String(payload.tool ?? ''),
        args: (payload.args as Record<string, unknown>) ?? {},
        status: 'pending',
      })
      break
    case 'confirm':
      assistant.confirms.push({
        request_id: String(payload.request_id ?? ''),
        tool: String(payload.tool ?? ''),
        summary: String(payload.summary ?? ''),
      })
      break
    case 'confirm_denied':
      assistant.confirms = []
      break
    case 'tool_result':
      if (assistant.tools.length) {
        const last = assistant.tools[assistant.tools.length - 1]
        last.status = String(payload.status ?? 'ok')
        last.result = String(payload.result ?? '')
      }
      break
    case 'error':
      assistant.error = String(payload.error ?? '')
      break
  }
}

async function confirmAction(requestId: string, decision: 'allow' | 'deny', assistant: Msg): Promise<void> {
  try {
    await api.aiAgentConfirm(requestId, decision)
    assistant.confirms = assistant.confirms.filter((c) => c.request_id !== requestId)
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

function stop(): void {
  controller?.abort()
}

onMounted(() => {
  void loadSessions()
})

onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="ai-panel">
    <div class="panel-header">
      <span class="panel-header-title">AI Agent{{ currentSessionTitle !== '新会话' ? ` · ${currentSessionTitle}` : '' }}</span>
      <div class="panel-header-actions">
        <IconButton title="会话历史" @click="showSessions = !showSessions"><History :size="14" /></IconButton>
        <IconButton title="新会话" @click="newSession"><Plus :size="14" /></IconButton>
      </div>
    </div>

    <div v-if="showSessions" class="agent-sessions">
      <div v-for="s in sessions" :key="s.id" class="agent-session-row">
        <button class="agent-session-name" :class="{ on: s.id === activeId }" @click="openSession(s.id)">
          {{ s.title }}
        </button>
        <IconButton title="删除" kind="danger" @click="deleteSession(s.id)"><Trash2 :size="12" /></IconButton>
      </div>
      <div v-if="!sessions.length" class="panel-empty">暂无会话</div>
    </div>

    <div class="ai-messages">
      <EmptyState v-if="messages.length === 0">
        <template #icon><Wrench :size="22" /></template>
        向 Agent 提问，可检索/读写知识库、调用工具；写操作会先请求确认
      </EmptyState>
      <div v-for="(m, i) in messages" :key="i" class="ai-msg" :class="m.role">
        <div class="ai-msg-role">{{ m.role === 'user' ? '你' : 'Agent' }}</div>
        <div class="ai-msg-content">
          <span v-if="m.content">{{ m.content }}</span>
          <span v-else>…</span>
          <div v-if="m.error" class="agent-error"><AlertTriangle :size="13" /> {{ m.error }}</div>
        </div>

        <!-- 工具执行日志 -->
        <div v-if="m.tools.length" class="agent-tools">
          <div v-for="(t, ti) in m.tools" :key="ti" class="agent-tool-card">
            <div class="agent-tool-head">
              <Wrench :size="11" />
              <span class="agent-tool-name">{{ t.tool }}</span>
              <span class="agent-tool-status" :class="t.status">{{ STATUS_LABEL[t.status ?? 'pending'] ?? t.status }}</span>
            </div>
            <div class="agent-tool-body">
              <div class="agent-tool-args">{{ argPreview(t.args) }}</div>
              <div v-if="t.result" class="agent-tool-result">{{ t.result }}</div>
            </div>
          </div>
        </div>

        <!-- 写操作确认（朱批） -->
        <div v-for="(c, ci) in m.confirms" :key="ci" class="agent-confirm">
          <div class="agent-confirm-text">
            <strong>Agent 请求执行写操作</strong>
            <span>{{ c.summary }}</span>
          </div>
          <div class="agent-confirm-meta">{{ c.tool }} · {{ argPreview({ request_id: c.request_id }) }}</div>
          <div class="agent-confirm-actions">
            <button class="btn btn--danger agent-confirm-allow" @click="confirmAction(c.request_id, 'allow', m)">允许</button>
            <button class="btn" @click="confirmAction(c.request_id, 'deny', m)">拒绝</button>
          </div>
        </div>
      </div>
    </div>

    <div class="ai-input-row">
      <input
        v-model="input"
        placeholder="向 Agent 提问（可读写知识库）…"
        :disabled="busy"
        @keyup.enter="send"
      />
      <IconButton v-if="busy" title="停止" @click="stop"><Square :size="14" /></IconButton>
      <IconButton v-else title="发送" @click="send"><Send :size="14" /></IconButton>
    </div>
  </div>
</template>
