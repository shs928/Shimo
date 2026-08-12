<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Search, Loader2 } from 'lucide-vue-next'
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

let timer: number | undefined

async function doSearch(): Promise<void> {
  const q = query.value.trim()
  if (!q) {
    results.value = []
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

function highlight(text: string, q: string): string {
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (!escaped) return text
  const re = new RegExp(`(${escaped})`, 'gi')
  return text.replace(re, '<mark>$1</mark>')
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

onMounted(() => {
  if (query.value.trim()) void doSearch()
})
</script>

<template>
  <div class="search-panel">
    <div class="search-input">
      <Search :size="14" class="search-icon" />
      <input
        v-model="query"
        placeholder="搜索标题、正文、标签、路径"
        @keydown="onKeydown"
      />
      <Loader2 v-if="loading" :size="14" class="spin search-icon" />
    </div>

    <div v-if="results.length === 0 && query.trim() && !loading" class="panel-empty">无匹配结果</div>

    <div class="search-results">
      <div
        v-for="(r, i) in results"
        :key="r.path"
        class="search-item"
        :class="{ active: i === activeIndex }"
        @click="openResult(r.path)"
        @mouseenter="activeIndex = i"
      >
        <div class="search-item-title" v-html="highlight(r.title || r.path, query.trim())" />
        <div class="search-item-path">{{ r.path }}</div>
        <div v-if="r.snippet" class="search-item-snippet" v-html="highlight(r.snippet, query.trim())" />
      </div>
    </div>
  </div>
</template>
