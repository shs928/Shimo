<script setup lang="ts">
import { computed } from 'vue'
import { Download, ExternalLink, FileText } from 'lucide-vue-next'
import { state } from '../store'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps<{ tabPath: string }>()

const tab = computed(() => state.tabs.find((t) => t.path === props.tabPath) ?? null)

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 原始文件在新窗口打开（浏览器原生渲染，不放松 X-Frame-Options） */
function openRaw(): void {
  window.open(`/api/v1/raw/${encodeURIComponent(props.tabPath)}`, '_blank', 'noopener')
}

function download(): void {
  const a = document.createElement('a')
  a.href = `/api/v1/raw/${encodeURIComponent(props.tabPath)}`
  a.download = tab.value?.name ?? props.tabPath
  a.click()
}
</script>

<template>
  <div class="doc-preview">
    <div class="doc-preview-head">
      <FileText :size="14" class="doc-preview-icon" />
      <span class="doc-preview-name">{{ tab?.name ?? tabPath }}</span>
      <span v-if="tab?.docMeta" class="doc-preview-meta">
        {{ fmtSize(tab.docMeta.size) }} · {{ tab.docMeta.chars.toLocaleString() }} 字符
      </span>
      <span class="doc-preview-actions">
        <IconButton title="在新窗口打开原文件" @click="openRaw"><ExternalLink :size="14" /></IconButton>
        <IconButton title="下载原文件" @click="download"><Download :size="14" /></IconButton>
      </span>
    </div>

    <!-- 纯文本预览：text interpolation，不使用 innerHTML -->
    <div class="doc-preview-body">
      <pre class="doc-preview-text">{{ tab?.content ?? '加载中…' }}</pre>
      <EmptyState v-if="tab?.docMeta?.truncated" class="empty-state--fill">
        预览已截断（超过 50 万字符），请在新窗口打开原文件查看完整内容
      </EmptyState>
    </div>
  </div>
</template>
