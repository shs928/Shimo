<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Download, ExternalLink, FileText, Loader } from 'lucide-vue-next'
import { api } from '../api'
import { state } from '../store'
import IconButton from './IconButton.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps<{ tabPath: string }>()

const tab = computed(() => state.tabs.find((t) => t.path === props.tabPath) ?? null)

const polling = ref(false)
let pollTimer: number | undefined

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const ocrActive = computed(() => {
  const meta = tab.value?.docMeta
  return !!meta && !meta.has_text && (meta.ocr_status === 'pending' || meta.ocr_status === 'running')
})

const ocrFailed = computed(() => {
  const meta = tab.value?.docMeta
  return !!meta && !meta.has_text && meta.ocr_status === 'failed'
})

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

/** 轮询 OCR 状态；识别完成后刷新标签内容 */
async function pollOcr(): Promise<void> {
  const t = tab.value
  if (!t) return
  try {
    const pv = await api.documentPreview(props.tabPath)
    t.content = pv.text
    t.savedContent = pv.text
    t.docMeta = {
      size: pv.size,
      chars: pv.chars,
      truncated: pv.truncated,
      has_text: pv.has_text,
      ocr: pv.ocr,
      ocr_status: pv.ocr_status,
      ocr_progress: pv.ocr_progress,
      ocr_error: pv.ocr_error,
    }
    stopPolling()
    if (pv.has_text || pv.ocr_status === 'failed') return
    if (pv.ocr_status === 'pending' || pv.ocr_status === 'running') {
      startPolling()
    }
  } catch {
    stopPolling()
  }
}

function startPolling(): void {
  if (pollTimer !== undefined) return
  polling.value = true
  pollTimer = window.setInterval(() => void pollOcr(), 4000)
}

function stopPolling(): void {
  window.clearInterval(pollTimer)
  pollTimer = undefined
  polling.value = false
}

/** 识别失败重试：重新入队并开始轮询 */
async function retryOcr(): Promise<void> {
  try {
    await api.ocrRetry(props.tabPath)
    const t = tab.value
    if (t?.docMeta) {
      t.docMeta.ocr_status = 'pending'
      t.docMeta.ocr_error = ''
    }
    startPolling()
    void pollOcr()
  } catch (e) {
    /* 失败保持原样，用户可再次重试 */
    console.error('ocr retry failed', e)
  }
}

watch(
  () => props.tabPath,
  () => {
    stopPolling()
    if (ocrActive.value) startPolling()
  },
)

onMounted(() => {
  if (ocrActive.value) startPolling()
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="doc-preview">
    <div class="doc-preview-head">
      <FileText :size="14" class="doc-preview-icon" />
      <span class="doc-preview-name">{{ tab?.name ?? tabPath }}</span>
      <span v-if="tab?.docMeta" class="doc-preview-meta">
        {{ fmtSize(tab.docMeta.size) }} · {{ tab.docMeta.chars.toLocaleString() }} 字符
        <template v-if="tab.docMeta.ocr"> · OCR 识别</template>
      </span>
      <span class="doc-preview-actions">
        <IconButton title="在新窗口打开原文件" @click="openRaw"><ExternalLink :size="14" /></IconButton>
        <IconButton title="下载原文件" @click="download"><Download :size="14" /></IconButton>
      </span>
    </div>

    <!-- 纯文本预览：text interpolation，不使用 innerHTML -->
    <div class="doc-preview-body">
      <template v-if="tab?.docMeta?.has_text">
        <pre class="doc-preview-text">{{ tab?.content ?? '加载中…' }}</pre>
        <EmptyState v-if="tab?.docMeta?.truncated" class="empty-state--fill">
          预览已截断（超过 50 万字符），请在新窗口打开原文件查看完整内容
        </EmptyState>
      </template>

      <!-- OCR 识别中 -->
      <EmptyState v-else-if="ocrActive" class="empty-state--fill" icon>
        <template #icon><Loader :size="18" class="spin" /></template>
        正在本地识别文字（OCR）… {{ tab?.docMeta?.ocr_progress ?? 0 }}%
        识别完成后自动显示文本并进入知识库索引。
      </EmptyState>

      <!-- OCR 失败 -->
      <EmptyState v-else-if="ocrFailed" class="empty-state--fill" icon>
        <template #icon><FileText :size="18" /></template>
        文字识别失败：{{ tab?.docMeta?.ocr_error || '未知错误' }}
        <template #actions>
          <button class="doc-open-original" @click="retryOcr">重新识别</button>
          <button class="doc-open-original doc-open-original--plain" @click="openRaw">
            <ExternalLink :size="14" /> 在浏览器中打开原文件
          </button>
        </template>
      </EmptyState>

      <!-- 无文字层（非 PDF 或未启用 OCR） -->
      <EmptyState v-else class="empty-state--fill" icon>
        <template #icon><FileText :size="18" /></template>
        该文档没有可提取的文字层（常见于扫描件 PDF），无法生成文本预览。
        可用浏览器原生阅读器直接查看原文。
        <template #actions>
          <button class="doc-open-original" @click="openRaw">
            <ExternalLink :size="14" /> 在浏览器中打开原文件
          </button>
        </template>
      </EmptyState>
    </div>
  </div>
</template>
