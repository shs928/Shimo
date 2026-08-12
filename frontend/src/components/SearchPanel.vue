<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Loader2, Search } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'

const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface Result {
  path: string
  title: string
  snippet: string
  rank: number
}

const query = ref('')
const results = ref<Result[]>([])
const loading = ref(false)
const activeIndex = ref(-1)
const listEl = ref<HTMLElement | null>(null)

let timer: number | undefined

async function doSearch(): Promise<void> {
  const q = query.value.trim()
  if (!q) {
    results.value = []
    activeIndex.value = -1
    return
  }
  loading.value = true
  try {
    const res = await api.search(q, 40)
    results.value = res.results
    activeIndex.value = results.value.length > 0 ? 0 : -1
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
    results.value = []
    activeIndex.value = -1
  } finally {
    loading.value = false
  }
}

watch(query, () => {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => void doSearch(), 250)
})

async function openResult(path: string): Promise<void> {
  try {
    await openTab(path)
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
  }
}

/**
 * 安全高亮：把命中片段拆成文本分片（命中 / 未命中），由 Vue 插值渲染。
 * 不使用 v-html，查询词永不被当作 HTML。
 */
function highlight(text: string): Array<{ seg: string; hit: boolean }> {
  const q = query.value.trim()
  if (!q) return [{ seg: text, hit: false }]
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  let re: RegExp
  try {
    re = new RegExp(`(${escaped})`, 'gi')
  } catch {
    return [{ seg: text, hit: false }]
  }
  const parts = text.split(re).filter(Boolean)
  const lowerQ = q.toLowerCase()
  return parts.map((seg) => ({ seg, hit: seg.toLowerCase() === lowerQ }))
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeIndex.value = Math.min(activeIndex.value + 1, results.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIndex.value = Math.max(activeIndex.value - 1, 0)
  } else if (e.key === 'Enter' && results.value[activeIndex.value]) {
    e.preventDefault()
    void openResult(results.value[activeIndex.value].path)
  }
}

watch(activeIndex, () => {
  listEl.value?.querySelector('.search-item.active')?.scrollIntoView({ block: 'nearest' })
})

onMounted(() => {
  if (query.value.trim()) void doSearch()
})

onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="search-panel">
    <div class="search-input">
      <Search :size="14" class="search-icon" />
      <input
        v-model="query"
        placeholder="搜索标题、正文、标签、路径"
        aria-label="全文搜索"
        @keydown="onKeydown"
      />
      <Loader2 v-if="loading" :size="14" class="spin search-icon" />
    </div>

    <div v-if="results.length === 0 && query.trim() && !loading" class="panel-empty">无匹配结果</div>

    <div ref="listEl" class="search-results" role="listbox" aria-label="搜索结果">
      <button
        v-for="(r, i) in results"
        :key="r.path"
        type="button"
        class="search-item"
        :class="{ active: i === activeIndex }"
        role="option"
        :aria-selected="i === activeIndex"
        @click="openResult(r.path)"
        @mouseenter="activeIndex = i"
      >
        <div class="search-item-title">
          <template v-for="(p, pi) in highlight(r.title || r.path)" :key="pi">
            <mark v-if="p.hit">{{ p.seg }}</mark>
            <template v-else>{{ p.seg }}</template>
          </template>
        </div>
        <div class="search-item-path">{{ r.path }}</div>
        <div v-if="r.snippet" class="search-item-snippet">
          <template v-for="(p, pi) in highlight(r.snippet)" :key="pi">
            <mark v-if="p.hit">{{ p.seg }}</mark>
            <template v-else>{{ p.seg }}</template>
          </template>
        </div>
      </button>
    </div>
  </div>
</template>
