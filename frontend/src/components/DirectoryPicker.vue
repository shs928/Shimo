<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ChevronDown, ChevronRight, Folder, FolderOpen, Home, Loader2 } from 'lucide-vue-next'
import { api } from '../api'
import type { NodeInfo } from '../types'

const props = withDefaults(defineProps<{ modelValue: string; label?: string }>(), { label: '保存位置' })
const emit = defineEmits<{ (e: 'update:modelValue', path: string): void }>()

const roots = ref<NodeInfo[]>([])
const children = reactive(new Map<string, NodeInfo[]>())
const expanded = reactive(new Set<string>())
const loading = reactive(new Set<string>())
const error = ref('')

interface DirRow {
  path: string
  name: string
  depth: number
}

const rows = computed<DirRow[]>(() => {
  const out: DirRow[] = []
  const walk = (items: NodeInfo[], depth: number) => {
    for (const item of items) {
      if (item.type !== 'dir') continue
      out.push({ path: item.path, name: item.name, depth })
      if (expanded.has(item.path)) walk(children.get(item.path) ?? [], depth + 1)
    }
  }
  walk(roots.value, 0)
  return out
})

async function load(path = ''): Promise<NodeInfo[]> {
  const result = await api.tree(path)
  return result.entries.filter((entry) => entry.type === 'dir')
}

async function toggle(path: string): Promise<void> {
  if (expanded.has(path)) {
    expanded.delete(path)
    return
  }
  expanded.add(path)
  if (children.has(path) || loading.has(path)) return
  loading.add(path)
  error.value = ''
  try {
    children.set(path, await load(path))
  } catch (e) {
    expanded.delete(path)
    error.value = (e as Error).message
  } finally {
    loading.delete(path)
  }
}

onMounted(async () => {
  try {
    roots.value = await load()
  } catch (e) {
    error.value = (e as Error).message
  }
})
</script>

<template>
  <fieldset class="directory-picker">
    <legend>{{ label }}</legend>
    <div class="directory-current" :title="modelValue || '知识库根目录'">
      <FolderOpen :size="14" /> {{ modelValue || '知识库根目录' }}
    </div>
    <div class="directory-tree" role="tree" aria-label="选择保存目录">
      <button
        type="button"
        class="directory-row"
        :class="{ on: modelValue === '' }"
        role="treeitem"
        aria-level="1"
        :aria-selected="modelValue === ''"
        @click="emit('update:modelValue', '')"
      >
        <span class="directory-toggle" />
        <Home :size="14" />
        <span>知识库根目录</span>
      </button>
      <div
        v-for="row in rows"
        :key="row.path"
        class="directory-row-wrap"
        :style="{ paddingLeft: `${row.depth * 16}px` }"
      >
        <button
          type="button"
          class="directory-toggle"
          :aria-label="expanded.has(row.path) ? `折叠 ${row.name}` : `展开 ${row.name}`"
          @click="toggle(row.path)"
        >
          <Loader2 v-if="loading.has(row.path)" :size="13" class="spin" />
          <ChevronDown v-else-if="expanded.has(row.path)" :size="13" />
          <ChevronRight v-else :size="13" />
        </button>
        <button
          type="button"
          class="directory-row directory-row--nested"
          :class="{ on: modelValue === row.path }"
          role="treeitem"
          :aria-level="row.depth + 2"
          :aria-expanded="expanded.has(row.path)"
          :aria-selected="modelValue === row.path"
          @click="emit('update:modelValue', row.path)"
        >
          <Folder :size="14" />
          <span>{{ row.name }}</span>
        </button>
      </div>
    </div>
    <p v-if="error" class="field-error" role="alert">目录加载失败：{{ error }}</p>
  </fieldset>
</template>
