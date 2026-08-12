<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Square, X } from 'lucide-vue-next'
import { api } from '../api'

const props = defineProps<{
  x: number
  y: number
  selectedText: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'apply', result: string, replace: boolean): void
  (e: 'notify', message: string, kind: 'info' | 'error'): void
}>()

const ACTIONS = [
  { id: 'continue', label: '✍ 续写' },
  { id: 'summary', label: '📝 提取摘要' },
  { id: 'brainstorm', label: '💡 头脑风暴' },
  { id: 'grammar', label: '✅ 语法/拼写修正' },
  { id: 'rewrite', label: '🔄 改写润色' },
]

const running = ref(false)
const result = ref('')
const error = ref('')
const currentAction = ref('')
const customPrompt = ref('')
let controller: AbortController | null = null

const menuStyle = computed(() => {
  const width = 260
  const x = Math.min(props.x, window.innerWidth - width - 8)
  const y = Math.min(props.y, window.innerHeight - 120)
  return { left: `${x}px`, top: `${y}px` }
})

async function run(action: string, label: string): Promise<void> {
  if (running.value) return
  currentAction.value = label
  result.value = ''
  error.value = ''
  running.value = true
  controller = new AbortController()
  try {
    const resp = await api.aiAction(props.selectedText, label, controller.signal)
    if (!resp.ok || !resp.body) {
      error.value = (await resp.text()).slice(0, 160) || '请求失败'
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
          if (payload.type === 'delta') result.value += payload.content
          else if (payload.type === 'error') error.value = payload.error
        } catch {
          /* ignore */
        }
      }
    }
  } catch (e) {
    const err = e as Error
    if (err.name !== 'AbortError') error.value = err.message
  } finally {
    running.value = false
    controller = null
  }
}

function stop(): void {
  controller?.abort()
}

function applyResult(replace: boolean): void {
  if (!result.value) return
  emit('apply', result.value, replace)
  emit('close')
}

function close(): void {
  controller?.abort()
  emit('close')
}

onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="ai-action-menu" :style="menuStyle" @click.stop>
    <div class="ai-action-menu-head">
      <span>AI 操作</span>
      <button class="icon-btn" title="关闭" @click="close"><X :size="13" /></button>
    </div>

    <template v-if="!running && !result">
      <button v-for="a in ACTIONS" :key="a.id" class="ai-action-item" @click="run(a.id, a.label)">
        {{ a.label }}
      </button>
      <div class="ai-action-custom">
        <input
          v-model="customPrompt"
          placeholder="自定义提示词，回车执行…"
          @keyup.enter="customPrompt.trim() && run('custom', customPrompt.trim())"
        />
      </div>
    </template>

    <template v-else>
      <div class="ai-action-status">{{ currentAction }}{{ running ? '…' : '' }}</div>
      <div class="ai-action-result">{{ error || result || '…' }}</div>
      <div v-if="result && !error" class="ai-action-actions">
        <button @click="applyResult(false)">插入到选中后</button>
        <button @click="applyResult(true)">替换选中</button>
        <button class="icon-btn" title="重新生成" @click="run('custom', currentAction)">↻</button>
      </div>
      <div v-if="error" class="ai-action-actions">
        <button @click="close">关闭</button>
      </div>
    </template>

    <button v-if="running" class="icon-btn ai-action-stop" title="停止" @click="stop"><Square :size="13" /></button>
  </div>
</template>
