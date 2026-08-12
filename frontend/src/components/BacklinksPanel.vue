<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Link2 } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'

const props = defineProps<{ tabPath: string }>()
const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Backlink {
  source_path: string
  title: string | null
  anchor: string
  alias: string
  link_type: string
  line: number
  context: string
}

const backlinks = ref<Backlink[]>([])
const loading = ref(false)

async function load(): Promise<void> {
  if (!props.tabPath) return
  loading.value = true
  try {
    const res = await api.backlinks(props.tabPath)
    backlinks.value = res.backlinks
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
    backlinks.value = []
  } finally {
    loading.value = false
  }
}

function openSource(path: string, anchor: string): void {
  void openTab(path)
    .then(() => {
      if (anchor) {
        window.setTimeout(() => {
          window.dispatchEvent(new CustomEvent('outline-jump', { detail: anchor }))
        }, 200)
      }
    })
    .catch((e) => emit('notify', (e as Error).message, 'error'))
}

function onSaved(e: Event): void {
  if ((e as CustomEvent<string>).detail === props.tabPath) void load()
}

watch(() => props.tabPath, () => void load(), { immediate: true })
onMounted(() => window.addEventListener('tab-saved', onSaved))
onBeforeUnmount(() => window.removeEventListener('tab-saved', onSaved))
</script>

<template>
  <div class="dock-panel">
    <div class="dock-title">
      <span>反向链接</span>
      <span class="dock-count">{{ backlinks.length }}</span>
    </div>
    <div class="dock-body">
      <div v-if="loading" class="panel-empty">加载中…</div>
      <div v-else-if="backlinks.length === 0" class="panel-empty">
        <Link2 :size="14" /> 暂无笔记链接到这里
      </div>
      <div v-for="(b, i) in backlinks" :key="i" class="backlink-item" @click="openSource(b.source_path, b.anchor)">
        <div class="backlink-title">{{ b.title || b.source_path }}</div>
        <div class="backlink-path">{{ b.source_path }}<span v-if="b.anchor"> → {{ b.anchor }}</span></div>
        <div v-if="b.context" class="backlink-context">{{ b.context }}</div>
      </div>
    </div>
  </div>
</template>
