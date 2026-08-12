<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { History, RotateCcw } from 'lucide-vue-next'
import { api } from '../api'
import PanelHeader from './PanelHeader.vue'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps<{ tabPath: string }>()
const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Version {
  sha1: string
  saved_at: string
}

const versions = ref<Version[]>([])
const loading = ref(false)
const selected = ref('')
const diffText = ref('')
const diffLoading = ref(false)

async function load(): Promise<void> {
  if (!props.tabPath) return
  loading.value = true
  try {
    const r = await api.historyList(props.tabPath)
    versions.value = r.versions
  } catch {
    versions.value = []
  } finally {
    loading.value = false
  }
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

async function showDiff(sha1: string): Promise<void> {
  selected.value = sha1
  diffLoading.value = true
  diffText.value = ''
  try {
    const r = await api.historyDiff(props.tabPath, sha1)
    diffText.value = r.diff || '（与当前内容一致）'
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  } finally {
    diffLoading.value = false
  }
}

async function restore(sha1: string): Promise<void> {
  const v = versions.value.find((x) => x.sha1 === sha1)
  if (!v) return
  if (!window.confirm(`恢复到 ${fmtTime(v.saved_at)} 的版本？当前内容将先保存为历史（可撤销）。`)) return
  try {
    const r = await api.historyRestore(props.tabPath, sha1)
    emit('notify', '已恢复历史版本', 'info')
    window.dispatchEvent(new CustomEvent('tree-refresh'))
    // 通知编辑器重载（当前标签内容被替换）
    window.dispatchEvent(new CustomEvent('open-note', { detail: r.path }))
    await load()
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

function onSaved(e: Event): void {
  if ((e as CustomEvent<string>).detail === props.tabPath) void load()
}

watch(() => props.tabPath, () => { selected.value = ''; diffText.value = ''; void load() }, { immediate: true })
onMounted(() => window.addEventListener('tab-saved', onSaved))
onBeforeUnmount(() => window.removeEventListener('tab-saved', onSaved))
</script>

<template>
  <div class="panel">
    <PanelHeader title="版本历史" :count="versions.length" />
    <div class="panel-body">
      <EmptyState v-if="loading" class="empty-state--fill">加载中…</EmptyState>
      <EmptyState v-else-if="versions.length === 0" class="empty-state--fill">
        <template #icon><History :size="18" /></template>
        暂无历史版本（保存时会自动留存旧版）
      </EmptyState>
      <template v-else>
        <div class="history-list">
          <div
            v-for="v in versions"
            :key="v.sha1"
            class="history-item"
            :class="{ on: selected === v.sha1 }"
            @click="showDiff(v.sha1)"
          >
            <span class="history-time">{{ fmtTime(v.saved_at) }}</span>
            <span class="history-meta mono">{{ v.sha1.slice(0, 10) }}</span>
            <IconButton title="恢复此版本" class="history-restore" @click.stop="restore(v.sha1)">
              <RotateCcw :size="12" />
            </IconButton>
          </div>
        </div>
        <div v-if="selected" class="history-diff">
          <div class="history-diff-title">
            与当前版本的差异{{ diffLoading ? '（加载中…）' : '' }}
          </div>
          <pre class="history-diff-text mono">{{ diffText || '…' }}</pre>
        </div>
      </template>
    </div>
  </div>
</template>
