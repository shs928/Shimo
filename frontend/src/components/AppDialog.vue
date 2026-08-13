<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import IconButton from './IconButton.vue'

const props = withDefaults(
  defineProps<{ open: boolean; title: string; description?: string; busy?: boolean; wide?: boolean }>(),
  { description: '', busy: false, wide: false },
)
const emit = defineEmits<{ (e: 'close'): void }>()
const dialog = ref<HTMLElement | null>(null)
const dialogId = `app-dialog-${Math.random().toString(36).slice(2, 9)}`
let previousFocus: HTMLElement | null = null

function close(): void {
  if (!props.busy) emit('close')
}

function focusables(): HTMLElement[] {
  return Array.from(
    dialog.value?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ) ?? [],
  )
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key !== 'Tab') return
  const items = focusables()
  if (!items.length) {
    event.preventDefault()
    dialog.value?.focus()
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousFocus = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      focusables()[0]?.focus() ?? dialog.value?.focus()
    } else {
      document.removeEventListener('keydown', onKeydown)
      previousFocus?.focus()
      previousFocus = null
    }
  },
)

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="app-dialog-layer" @mousedown.self="close">
      <section
        ref="dialog"
        class="app-dialog"
        :class="{ 'app-dialog--wide': wide }"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="`${dialogId}-title`"
        :aria-describedby="description ? `${dialogId}-description` : undefined"
        tabindex="-1"
      >
        <header class="app-dialog-head">
          <div>
            <h2 :id="`${dialogId}-title`">{{ title }}</h2>
            <p v-if="description" :id="`${dialogId}-description`">{{ description }}</p>
          </div>
          <IconButton title="关闭" :disabled="busy" @click="close"><X :size="16" /></IconButton>
        </header>
        <div class="app-dialog-body"><slot /></div>
        <footer v-if="$slots.footer" class="app-dialog-foot"><slot name="footer" /></footer>
      </section>
    </div>
  </Teleport>
</template>
