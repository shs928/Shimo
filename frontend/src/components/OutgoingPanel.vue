<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ExternalLink, FileQuestion } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'
import PanelHeader from './PanelHeader.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps<{ tabPath: string }>()
const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Outgoing {
  target_raw: string
  target_path: string | null
  anchor: string
  alias: string
  link_type: string
  line: number
  context: string
  resolved: number
}

const links = ref<Outgoing[]>([])
const loading = ref(false)

async function load(): Promise<void> {
  if (!props.tabPath) return
  loading.value = true
  try {
    const res = await api.outgoing(props.tabPath)
    links.value = res.links
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
    links.value = []
  } finally {
    loading.value = false
  }
}

function openTarget(l: Outgoing): void {
  if (!l.target_path) return
  void openTab(l.target_path)
    .then(() => {
      if (l.anchor) {
        window.setTimeout(() => {
          window.dispatchEvent(new CustomEvent('outline-jump', { detail: l.anchor }))
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
    <PanelHeader title="链接" :count="links.length" />
    <div class="panel-body">
      <EmptyState v-if="loading" class="empty-state--fill">加载中…</EmptyState>
      <EmptyState v-else-if="links.length === 0" class="empty-state--fill">当前笔记没有链接</EmptyState>
      <template v-else>
        <div v-for="(l, i) in links" :key="i" class="backlink-item" @click="openTarget(l)">
          <div class="backlink-title">
            {{ l.alias || l.target_raw }}
            <FileQuestion v-if="!l.resolved" :size="12" class="warn-icon" title="目标不存在或存在歧义" />
          </div>
          <div class="backlink-path">
            {{ l.link_type }}
            <span v-if="l.target_path" class="outgoing-target">
              <ExternalLink :size="11" /> {{ l.target_path }}
            </span>
            <span v-else class="outgoing-unresolved">未解析</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
