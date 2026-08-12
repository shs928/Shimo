<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Link2 } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'
import PanelHeader from './PanelHeader.vue'
import EmptyState from './EmptyState.vue'

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
  <div class="panel">
    <PanelHeader title="反向链接" :count="backlinks.length" />
    <div class="panel-body">
      <EmptyState v-if="loading" class="empty-state--fill">加载中…</EmptyState>
      <EmptyState v-else-if="backlinks.length === 0" class="empty-state--fill">
        <template #icon><Link2 :size="18" /></template>
        暂无笔记链接到这里
      </EmptyState>
      <template v-else>
        <div v-for="(b, i) in backlinks" :key="i" class="backlink-item" @click="openSource(b.source_path, b.anchor)">
          <div class="backlink-title">{{ b.title || b.source_path }}</div>
          <div class="backlink-path">{{ b.source_path }}<span v-if="b.anchor"> → {{ b.anchor }}</span></div>
          <div v-if="b.context" class="backlink-context">{{ b.context }}</div>
        </div>
      </template>
    </div>
  </div>
</template>
