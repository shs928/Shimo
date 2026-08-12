<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Plus, RefreshCw, Send, Square, Trash2, Wrench } from 'lucide-vue-next'
import { api } from '../api'

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

const sessions = ref<SessionInfo[]>([])
const activeId = ref('')
const messages = ref<Msg[]>([])
const input = ref('')
const busy = ref(false)
const showSessions = ref(false)
const currentSessionTitle = ref('新会话')
let controller: AbortController | null = null

function renderContent(text: string): string {
  return text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>')
}

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

function newSession(): void {
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
    if (activeId.value === id) newSession()
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

  let body: ReadableStreamDefaultReader<Uint8Array> | null = null
  try {
    const resp = await api.aiAgentChat(text, activeId.value || undefined, controller.signal)
    if (!resp.ok || !resp.body) {
      assistant.error = (await resp.text()).slice(0, 160) || '请求失败'
      return
    }
    body = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (body) {
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
    <div class="dock-title">
      <span>AI Agent</span>
      <button class="icon-btn ai-config-btn" title="会话列表" @click="showSessions = !showSessions">
        <Plus :size="14" />
      </button>
      <button class="icon-btn" title="新会话" @click="newSession"><RefreshCw :size="14" /></button>
    </div>

    <div v-if="showSessions" class="agent-sessions">
      <div v-for="s in sessions" :key="s.id" class="agent-session-row">
        <button class="agent-session-name" @click="openSession(s.id)">{{ s.title }}</button>
        <button class="icon-btn danger" title="删除" @click="deleteSession(s.id)"><Trash2 :size="12" /></button>
      </div>
      <div v-if="!sessions.length" class="panel-empty">暂无会话</div>
    </div>

    <div class="ai-messages">
      <div v-if="messages.length === 0" class="panel-empty">
        向 Agent 提问，可检索/读写知识库、调用工具
      </div>
      <div v-for="(m, i) in messages" :key="i" class="ai-msg" :class="m.role">
        <div class="ai-msg-role">{{ m.role === 'user' ? '我' : 'Agent' }}</div>
        <div class="ai-msg-content">
          <span v-if="m.content" v-html="renderContent(m.content)" />
          <span v-else>…</span>
          <div v-if="m.error" class="agent-error">⚠️ {{ m.error }}</div>
        </div>

        <!-- 工具调用 -->
        <div v-if="m.tools.length" class="agent-tools">
          <div v-for="(t, ti) in m.tools" :key="ti" class="agent-tool-card">
            <div class="agent-tool-head">
              <Wrench :size="11" />
              <span class="mono">{{ t.tool }}</span>
              <span class="agent-tool-status" :class="t.status">{{ t.status }}</span>
            </div>
            <div class="agent-tool-args mono">{{ argPreview(t.args) }}</div>
            <div v-if="t.result" class="agent-tool-result">{{ t.result }}</div>
          </div>
        </div>

        <!-- 写操作确认 -->
        <div v-for="(c, ci) in m.confirms" :key="ci" class="agent-confirm">
          <div class="agent-confirm-text">⚠️ Agent 请求执行：{{ c.summary }}</div>
          <div class="agent-confirm-actions">
            <button class="agent-confirm-allow" @click="confirmAction(c.request_id, 'allow', m)">允许</button>
            <button @click="confirmAction(c.request_id, 'deny', m)">拒绝</button>
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
      <button v-if="busy" class="icon-btn" title="停止" @click="stop"><Square :size="14" /></button>
      <button v-else class="icon-btn" title="发送" @click="send"><Send :size="14" /></button>
    </div>
  </div>
</template>
