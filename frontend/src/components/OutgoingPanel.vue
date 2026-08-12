<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ExternalLink, FileQuestion } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'

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
  <div class="dock-panel">
    <div class="dock-title">
      <span>链接</span>
      <span class="dock-count">{{ links.length }}</span>
    </div>
    <div class="dock-body">
      <div v-if="loading" class="panel-empty">加载中…</div>
      <div v-else-if="links.length === 0" class="panel-empty">当前笔记没有链接</div>
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
    </div>
  </div>
</template>
