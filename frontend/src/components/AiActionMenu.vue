<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CheckCircle2, Lightbulb, PenLine, RefreshCw, Square, Wand2, X } from 'lucide-vue-next'
import { api } from '../api'
import IconButton from './IconButton.vue'

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
  { id: 'continue', label: '续写', icon: PenLine },
  { id: 'summary', label: '提取摘要', icon: CheckCircle2 },
  { id: 'brainstorm', label: '头脑风暴', icon: Lightbulb },
  { id: 'grammar', label: '语法/拼写修正', icon: Wand2 },
  { id: 'rewrite', label: '改写润色', icon: RefreshCw },
]

const running = ref(false)
const result = ref('')
const error = ref('')
const currentAction = ref('')
const customPrompt = ref('')
const focusedIndex = ref(0)
const menuEl = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

/** 视口碰撞：菜单始终完整落在视口内 */
const menuStyle = computed(() => {
  const width = 264
  const height = 340
  const x = Math.max(8, Math.min(props.x, window.innerWidth - width - 8))
  const y = Math.max(8, Math.min(props.y, window.innerHeight - height - 8))
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

/** 键盘导航：↑↓ 循环、Enter 执行、Escape 关闭 */
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    return
  }
  if (running.value || result.value) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    focusedIndex.value = (focusedIndex.value + 1) % ACTIONS.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    focusedIndex.value = (focusedIndex.value - 1 + ACTIONS.length) % ACTIONS.length
  } else if (e.key === 'Enter') {
    const a = ACTIONS[focusedIndex.value]
    if (a) {
      e.preventDefault()
      void run(a.id, a.label)
    }
  }
}

onMounted(() => {
  menuEl.value?.querySelector<HTMLElement>('.ai-action-item')?.focus()
  menuEl.value?.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  controller?.abort()
  menuEl.value?.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    ref="menuEl"
    class="ai-action-menu"
    :style="menuStyle"
    role="menu"
    aria-label="AI 操作"
    tabindex="-1"
    @click.stop
  >
    <div class="ai-action-menu-head">
      <span>AI 操作</span>
      <IconButton title="关闭 (Esc)" @click="close"><X :size="13" /></IconButton>
    </div>

    <template v-if="!running && !result">
      <button
        v-for="(a, i) in ACTIONS"
        :key="a.id"
        class="ai-action-item"
        :class="{ focused: i === focusedIndex }"
        role="menuitem"
        @click="run(a.id, a.label)"
      >
        <component :is="a.icon" :size="13" />
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
        <button class="btn" @click="applyResult(false)">插入到选中后</button>
        <button class="btn" @click="applyResult(true)">替换选中</button>
        <IconButton title="重新生成" @click="run('custom', currentAction)"><RefreshCw :size="13" /></IconButton>
      </div>
      <div v-if="error" class="ai-action-actions">
        <button class="btn" @click="close">关闭</button>
      </div>
    </template>

    <IconButton v-if="running" class="ai-action-stop" title="停止" @click="stop"><Square :size="13" /></IconButton>
  </div>
</template>
