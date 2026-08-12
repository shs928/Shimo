<script setup lang="ts">
import { ref, watch } from 'vue'
import { Bot, BookOpen, StickyNote } from 'lucide-vue-next'

export type SheetKind = 'none' | 'index' | 'marginalia' | 'assistant'

const props = defineProps<{ sheet: SheetKind }>()
const emit = defineEmits<{
  (e: 'open', which: 'index' | 'marginalia' | 'assistant'): void
  (e: 'close'): void
}>()

/** 记录最后触发的按钮，关闭后焦点返回 */
const lastTrigger = ref<HTMLButtonElement | null>(null)

function trigger(btn: HTMLButtonElement | null, which: 'index' | 'marginalia' | 'assistant'): void {
  lastTrigger.value = btn
  if (props.sheet === which) emit('close')
  else emit('open', which)
}

watch(
  () => props.sheet,
  (next, prev) => {
    if (prev !== 'none' && next === 'none') {
      lastTrigger.value?.focus()
    }
  },
)
</script>

<template>
  <nav class="mobile-workbench" aria-label="移动工作台">
    <button
      class="mw-btn"
      :class="{ on: sheet === 'index' }"
      @click="trigger(($event.currentTarget as HTMLButtonElement), 'index')"
    >
      <BookOpen :size="18" />
      <span>目录</span>
    </button>
    <button
      class="mw-btn"
      :class="{ on: sheet === 'marginalia' }"
      @click="trigger(($event.currentTarget as HTMLButtonElement), 'marginalia')"
    >
      <StickyNote :size="18" />
      <span>边注</span>
    </button>
    <button
      class="mw-btn"
      :class="{ on: sheet === 'assistant' }"
      @click="trigger(($event.currentTarget as HTMLButtonElement), 'assistant')"
    >
      <Bot :size="18" />
      <span>助手</span>
    </button>
  </nav>
</template>
