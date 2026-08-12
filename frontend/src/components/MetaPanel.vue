<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps<{ tabPath: string }>()

interface Meta {
  path: string
  title: string
  frontmatter: Record<string, unknown>
  has_frontmatter: boolean
  size: number
  mtime_ns: number
  etag: string
}

const meta = ref<Meta | null>(null)

async function load(): Promise<void> {
  meta.value = null
  try {
    meta.value = await api.fileMeta(props.tabPath)
  } catch {
    meta.value = null
  }
}

const kvEntries = computed(() => {
  const fm = meta.value?.frontmatter ?? {}
  return Object.entries(fm).filter(([k]) => !['title'].includes(k))
})

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmtTime(ns: number): string {
  return new Date(ns / 1e6).toLocaleString()
}

function onSaved(e: Event): void {
  if ((e as CustomEvent<string>).detail === props.tabPath) void load()
}

watch(() => props.tabPath, () => void load(), { immediate: true })
onMounted(() => window.addEventListener('tab-saved', onSaved))
onBeforeUnmount(() => window.removeEventListener('tab-saved', onSaved))
</script>

<template>
  <div class="meta-panel">
    <div class="panel-title">属性</div>
    <template v-if="meta">
      <div class="meta-group">
        <div class="meta-kv"><span>标题</span><span class="meta-val">{{ meta.title || '—' }}</span></div>
        <div v-for="[k, v] in kvEntries" :key="k" class="meta-kv">
          <span>{{ k }}</span>
          <span class="meta-val" :title="String(v)">{{ String(v) }}</span>
        </div>
        <div v-if="kvEntries.length === 0" class="panel-empty">无额外属性</div>
      </div>
      <div class="meta-group">
        <div class="meta-kv"><span>大小</span><span class="meta-val">{{ fmtSize(meta.size) }}</span></div>
        <div class="meta-kv"><span>修改时间</span><span class="meta-val">{{ fmtTime(meta.mtime_ns) }}</span></div>
        <div class="meta-kv"><span>ETag</span><span class="meta-val mono" :title="meta.etag">{{ meta.etag.slice(0, 14) }}…</span></div>
      </div>
      <p class="meta-hint">在文件顶部用 <code>---</code> 写 YAML 属性，保存后自动展示。</p>
    </template>
    <div v-else class="panel-empty">无文件信息</div>
  </div>
</template>
